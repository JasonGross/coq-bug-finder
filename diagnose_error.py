from __future__ import with_statement, print_function
import os, sys, tempfile, subprocess, re, time, math, glob, threading
from Popen_noblock import Popen_async, Empty
from memoize import memoize
from file_util import clean_v_file
from util import re_escape
from custom_arguments import DEFAULT_LOG
import util

__all__ = ["has_error", "get_error_line_number", "get_error_byte_locations", "make_reg_string", "get_coq_output", "get_coq_output_iterable", "get_error_string", "get_timeout", "reset_timeout", "reset_coq_output_cache"]

DEFAULT_PRE_PRE_ERROR_REG_STRING = 'File "[^"]+", line ([0-9]+), characters [0-9-]+:\n'
DEFAULT_PRE_ERROR_REG_STRING = 'File "[^"]+", line ([0-9]+), characters [0-9-]+:\n(?!Warning)'
DEFAULT_PRE_ERROR_REG_STRING_WITH_BYTES = 'File "[^"]+", line ([0-9]+), characters ([0-9]+)-([0-9]+):\n(?!Warning)'
DEFAULT_ERROR_REG_STRING = DEFAULT_PRE_ERROR_REG_STRING + '((?:.|\n)+)'
DEFAULT_ERROR_REG_STRING_WITH_BYTES = DEFAULT_PRE_ERROR_REG_STRING_WITH_BYTES + '((?:.|\n)+)'
DEFAULT_ERROR_REG_STRING_GENERIC = DEFAULT_PRE_PRE_ERROR_REG_STRING + '(%s)'

def clean_output(output):
    return util.normalize_newlines(output)

@memoize
def get_coq_accepts_fine_grained_debug(coqc, debug_kind):
    temp_file = tempfile.NamedTemporaryFile(suffix='.v', dir='.', delete=True)
    temp_file_name = temp_file.name
    p = subprocess.Popen([coqc, "-q", "-d", debug_kind, temp_file_name], stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
    (stdout, stderr) = p.communicate()
    temp_file.close()
    clean_v_file(temp_file_name)
    return 'Unknown option -d' not in util.s(stdout) and '-d: no such file or directory' not in util.s(stdout) and 'There is no debug flag' not in util.s(stdout)

def get_coq_debug_native_compiler_args(coqc):
    if get_coq_accepts_fine_grained_debug(coqc, "native-compiler"): return ["-d", "native-compiler"]
    return ["-debug"]

@memoize
def get_error_match(output, reg_string=DEFAULT_ERROR_REG_STRING, pre_reg_string=DEFAULT_PRE_ERROR_REG_STRING):
    """Returns the final match of reg_string"""
    locations = [0] + [m.start() for m in re.finditer(pre_reg_string, output)]
    reg = re.compile(reg_string)
    results = (reg.search(output[start_loc:]) for start_loc in reversed(locations))
    for result in results:
        if result: return result
    return None

@memoize
def has_error(output, reg_string=DEFAULT_ERROR_REG_STRING, pre_reg_string=DEFAULT_PRE_ERROR_REG_STRING):
    """Returns True if the coq output encoded in output has an error
    matching the given regular expression, False otherwise.
    """
    errors = get_error_match(output, reg_string=reg_string, pre_reg_string=pre_reg_string)
    if errors:
        return True
    else:
        return False

@memoize
def get_error_line_number(output, reg_string=DEFAULT_ERROR_REG_STRING, pre_reg_string=DEFAULT_PRE_ERROR_REG_STRING):
    """Returns the line number that the error matching reg_string
    occured on.

    Precondition: has_error(output, reg_string)
    """
    errors = get_error_match(output, reg_string=reg_string, pre_reg_string=pre_reg_string)
    return int(errors.groups()[0])

@memoize
def get_error_byte_locations(output, reg_string=DEFAULT_ERROR_REG_STRING_WITH_BYTES, pre_reg_string=DEFAULT_PRE_ERROR_REG_STRING_WITH_BYTES):
    """Returns the byte locations that the error matching reg_string
    occured on.

    Precondition: has_error(output, reg_string)
    """
    errors = get_error_match(output, reg_string=reg_string, pre_reg_string=pre_reg_string)
    return (int(errors.groups()[1]), int(errors.groups()[2]))

@memoize
def get_error_string(output, reg_string=DEFAULT_ERROR_REG_STRING, pre_reg_string=DEFAULT_PRE_ERROR_REG_STRING):
    """Returns the error string of the error matching reg_string.

    Precondition: has_error(output, reg_string)
    """
    errors = get_error_match(output, reg_string=reg_string, pre_reg_string=pre_reg_string)
    return errors.groups()[1]

@memoize
def make_reg_string(output, strict_whitespace=False):
    """Returns a regular expression for matching the particular error
    in output.

    Precondition: has_error(output)
    """
    unstrictify_whitespace = (lambda s: s)
    if not strict_whitespace:
        unstrictify_whitespace = (lambda s: re.sub(r'(?:\\s\\+)+', r'\\s+', re.sub(r'(?:\\ )+', r'\\s+', re.sub(r'(\\n|\n)(?:\\ )+', r'\\s+', s.replace('\\\n', '\n')))).replace('\n', '\s').replace(r'\s+\s', r'\s+').replace(r'\s++', r'\s+'))

    error_string = get_error_string(output).strip()
    if not util.PY3: error_string = error_string.decode('utf-8')
    if 'Universe inconsistency' in error_string or 'universe inconsistency' in error_string:
        re_string = re.sub(r'([Uu]niverse\\ inconsistency.*) because(.|\n)*',
                           r'\1 because.*',
                           re_escape(error_string))
        re_string = re.sub(r'(\s)[^\s]+?\.([0-9]+)',
                           r'\1[^\\s]+?\\.\2',
                           re_string)
    elif 'Unsatisfied constraints' in error_string:
        re_string = re.sub(r'Error:\\ Unsatisfied\\ constraints:.*(?:\n.+)*.*\\\(maybe\\ a\\ bugged\\ tactic\\\)',
                           r'Error:\\ Unsatisfied\\ constraints:.*(?:\\n.+)*.*\\(maybe\\ a\\ bugged\\ tactic\\)',
                           re_escape(error_string),
                           re.DOTALL)
    elif re.search(r'Universe [^ ]* is unbound', error_string):
        re_string = re.sub(r'Universe\\ [^ ]*\\ is\\ unbound',
                           r'Universe\\ [^ ]*\\ is\\ unbound',
                           re_escape(error_string))
    else:
        re_string = re_escape(error_string)
    re_string = re.sub(r'tmp(?:[A-Za-z\d]|\\_)+',
                       r'tmp[A-Za-z_\\d]+',
                       re_string)
    if r'Universe\ instance\ should\ have\ length\ ' not in re_string:
        re_string = re.sub(r'[\d]+',
                           r'[\\d]+',
                           re_string)
    ret = DEFAULT_ERROR_REG_STRING_GENERIC % unstrictify_whitespace(re_string)
    if not util.PY3: ret = ret.encode('utf-8')
    return ret

TIMEOUT = None

def get_timeout():
    return TIMEOUT

def reset_timeout():
    global TIMEOUT
    TIMEOUT = None

def timeout_Popen_communicate(log, *args, **kwargs):
    ret = { 'value' : ('', ''), 'returncode': None }
    timeout = kwargs.get('timeout')
    del kwargs['timeout']
    input_val = kwargs.get('input')
    if input_val is not None: input_val = input_val.encode('utf-8')
    del kwargs['input']
    p = subprocess.Popen(*args, **kwargs)

    def target():
        ret['value'] = tuple(map(util.s, p.communicate(input=input_val)))
        ret['returncode'] = p.returncode

    thread = threading.Thread(target=target)
    thread.start()

    thread.join(timeout)
    if not thread.is_alive():
        return (ret['value'], ret['returncode'])

    p.terminate()
    thread.join()
    return (tuple(map((lambda s: (s if s else '') + '\nTimeout!'), ret['value'])), ret['returncode'])


def memory_robust_timeout_Popen_communicate(log, *args, **kwargs):
    while True:
        try:
            return timeout_Popen_communicate(log, *args, **kwargs)
        except OSError as e:
            log('Warning: subprocess.Popen%s%s failed with %s\nTrying again in 10s' % (repr(tuple(args)), repr(kwargs), repr(e)), force_stdout=True)
            time.sleep(10)

COQ_OUTPUT = {}

def sanitize_cmd(cmd):
    return re.sub(r'("/tmp/tmp)[^ "]*?(\.v")', r'\1XXXXXXXX\2', cmd)

prepare_cmds_for_coq_output_printed_cmd_already = set()
def prepare_cmds_for_coq_output(coqc_prog, coqc_prog_args, contents, cwd=None, timeout_val=0, **kwargs):
    key = (coqc_prog, tuple(coqc_prog_args), kwargs['pass_on_stdin'], contents, timeout_val, cwd)
    if key in COQ_OUTPUT.keys():
        file_name = COQ_OUTPUT[key][0]
    else:
        with tempfile.NamedTemporaryFile(suffix='.v', delete=False, mode='wb') as f:
            f.write(contents.encode('utf-8'))
            file_name = f.name

    file_name_root = os.path.splitext(file_name)[0]

    cmds = [coqc_prog] + list(coqc_prog_args)
    pseudocmds = ''
    input_val = None
    if kwargs['is_coqtop']:
        if kwargs['pass_on_stdin']:
            input_val = contents
            cmds.extend(['-q'])
            pseudocmds = '" < "%s' % file_name
        else:
            cmds.extend(['-load-vernac-source', file_name_root, '-q'])
    else:
        cmds.extend([file_name, '-q'])
    cmd_to_print = '"%s%s"' % ('" "'.join(cmds), pseudocmds)
    if kwargs['verbose'] >= kwargs['verbose_base'] or (kwargs['verbose'] >= kwargs['verbose_base'] - 1 and sanitize_cmd(cmd_to_print) not in prepare_cmds_for_coq_output_printed_cmd_already):
        prepare_cmds_for_coq_output_printed_cmd_already.add(sanitize_cmd(cmd_to_print))
        kwargs['log']('\nRunning command: %s' % cmd_to_print)
    if kwargs['verbose'] >= kwargs['verbose_base'] + 1:
        kwargs['log']('\nContents:\n%s\n' % contents)

    return key, file_name, cmds, input_val

def reset_coq_output_cache(coqc_prog, coqc_prog_args, contents, timeout_val, cwd=None, is_coqtop=False, pass_on_stdin=False, verbose_base=1, **kwargs):
    key, file_name, cmds, input_val = prepare_cmds_for_coq_output(coqc_prog, coqc_prog_args, contents, cwd=cwd, timeout_val=timeout_val, is_coqtop=is_coqtop, pass_on_stdin=pass_on_stdin, verbose_base=verbose_base, **kwargs)

    if key in COQ_OUTPUT.keys(): del COQ_OUTPUT[key]

def get_coq_output(coqc_prog, coqc_prog_args, contents, timeout_val, cwd=None, is_coqtop=False, pass_on_stdin=False, verbose_base=1, retry_with_debug_when=(lambda output: 'is not a compiled interface for this version of OCaml' in output), **kwargs):
    """Returns the coqc output of running through the given
    contents.  Pass timeout_val = None for no timeout."""
    global TIMEOUT
    if timeout_val is not None and timeout_val < 0 and TIMEOUT is not None:
        return get_coq_output(coqc_prog, coqc_prog_args, contents, TIMEOUT, cwd=cwd, is_coqtop=is_coqtop, pass_on_stdin=pass_on_stdin, verbose_base=verbose_base, retry_with_debug_when=retry_with_debug_when, **kwargs)

    key, file_name, cmds, input_val = prepare_cmds_for_coq_output(coqc_prog, coqc_prog_args, contents, cwd=cwd, timeout_val=timeout_val, is_coqtop=is_coqtop, pass_on_stdin=pass_on_stdin, verbose_base=verbose_base, **kwargs)

    if key in COQ_OUTPUT.keys(): return COQ_OUTPUT[key][1]

    start = time.time()
    ((stdout, stderr), returncode) = memory_robust_timeout_Popen_communicate(kwargs['log'], cmds, stderr=subprocess.STDOUT, stdout=subprocess.PIPE, stdin=subprocess.PIPE, timeout=(timeout_val if timeout_val is not None and timeout_val > 0 else None), input=input_val, cwd=cwd)
    finish = time.time()
    if kwargs['verbose'] >= verbose_base + 1:
        kwargs['log']('\nretcode: %d\nstdout:\n%s\n\nstderr:\n%s\n\n' % (returncode, util.s(stdout), util.s(stderr)))
    if TIMEOUT is None and timeout_val is not None:
        TIMEOUT = 3 * max((1, int(math.ceil(finish - start))))
    clean_v_file(file_name)
    COQ_OUTPUT[key] = (file_name, (clean_output(util.s(stdout)), tuple(cmds), returncode))
    if kwargs['verbose'] >= verbose_base + 2: kwargs['log']('Storing result: COQ_OUTPUT[%s]:\n%s' % (repr(key), repr(COQ_OUTPUT[key])))
    if retry_with_debug_when(COQ_OUTPUT[key][1][0]):
        debug_args = get_coq_debug_native_compiler_args(coqc_prog)
        if kwargs['verbose'] >= verbose_base - 1: kwargs['log']('Retrying with %s...' % ' '.join(debug_args))
        return get_coq_output(coqc_prog, list(debug_args) + list(coqc_prog_args), contents, timeout_val, cwd=cwd, is_coqtop=is_coqtop, pass_on_stdin=pass_on_stdin, verbose_base=verbose_base, retry_with_debug_when=(lambda output: False), **kwargs)
    return COQ_OUTPUT[key][1]

def get_coq_output_iterable(coqc_prog, coqc_prog_args, contents, cwd=None, is_coqtop=False, pass_on_stdin=False, verbose_base=1, sep='\nCoq <', **kwargs):
    """Returns the coqc output of running through the given
    contents."""

    key, file_name, cmds, input_val = prepare_cmds_for_coq_output(coqc_prog, coqc_prog_args, contents, cwd=cwd, timeout_val=None, is_coqtop=is_coqtop, pass_on_stdin=pass_on_stdin, verbose_base=verbose_base, **kwargs)

    if key in COQ_OUTPUT.keys():
        for i in COQ_OUTPUT[key][1].split(sep): yield i

    p = Popen_async(cmds, stderr=subprocess.STDOUT, stdout=subprocess.PIPE, stdin=subprocess.PIPE, cwd=cwd)

    so_far = []
    cur = ''
    yielded = True
    completed = False
    while True:
        if yielded and not completed:
            if input_val:
                i = input_val.index('\n') + 1 if '\n' in input_val else len(input_val)
                if kwargs['verbose'] >= 3: print(input_val[:i], end='')
                p.stdin.write(input_val[:i])
                input_val = input_val[i:]
                p.stdin.flush()
                yielded = False
            else:
                completed = True
                p.stdin.close()
        curv = p.stdout.get(True)
        if kwargs['verbose'] >= 3: print(curv, end='')
        cur += curv
        if sep in cur:
            vals = cur.split(sep)
            for val in vals[:-1]:
                yield val
                yielded = True
                so_far.append(val)
            cur = vals[-1]
    if cur != '':
        yield cur
        so_far.append(cur)
    clean_v_file(file_name)
    ## remove instances of the file name
    #stdout = stdout.replace(os.path.basename(file_name[:-2]), 'Top')
    COQ_OUTPUT[key] = (file_name, (clean_output(sep.join(so_far)), tuple(cmds), None))
