from __future__ import print_function
import sys, os
from argparse_compat import argparse

__all__ = ["add_libname_arguments", "ArgumentParser", "update_env_with_libnames", "add_logging_arguments", "process_logging_arguments", "DEFAULT_LOG", "DEFAULT_VERBOSITY"]

# grumble, grumble, we want to support multiple -R arguments like coqc
class CoqLibnameAction(argparse.Action):
    non_default = False
#     def __init__(self, *args, **kwargs):
#         super(CoqLibnameAction, self).__init__(*args, **kwargs)
    def __call__(self, parser, namespace, values, option_string=None):
#        print('%r %r %r' % (namespace, values, option_string))
        if not self.non_default:
            setattr(namespace, self.dest, [])
            self.non_default = True
        getattr(namespace, self.dest).append(tuple(values))

DEFAULT_VERBOSITY=1

def make_logger(log_files):
    def log(text, force_stdout=False):
        for i in log_files:
            if force_stdout and i.fileno() == 2: continue # skip stderr if we write to stdout
            i.write(str(text) + '\n')
            i.flush()
            if i.fileno() > 2: # stderr
                os.fsync(i.fileno())
        if force_stdout and not any(i.fileno() == 1 for i in log_files): # not already writing to stdout
            print(text)
    return log

DEFAULT_LOG = make_logger([sys.stderr])

class DeprecatedAction(argparse.Action):
    def __init__(self, replacement=None, *args, **kwargs):
        if replacement is None:
            raise ValueError("replacement must not be None")
        super(DeprecatedAction, self).__init__(*args, **kwargs)
        self.replacement = replacement
    def __call__(self, parser, namespace, values, option_string=None):
        print('ERROR: option %s is deprecated.  Use %s instead.' % (option_string, self.replacement), file=sys.stderr)
        sys.exit(0)

class ArgAppendWithWarningAction(argparse.Action):
    def __call__(self, parser, namespace, value, option_string=None):
        items = getattr(namespace, self.dest) or []
        items.append(value)
        setattr(namespace, self.dest, items)
        if value.startswith('-w '):
            print(('WARNING: You seem to be trying to pass a warning list to Coq via a single %s ("%s").' +
                   '\n  I will continue anyway, but this will most likely not work.' +
                   '\n  Instead try using multiple invocations, as in' +
                   '\n    %s')
                  % (option_string, value, ' '.join(option_string + '=' + v for v in value.split(' '))),
                  file=sys.stderr)

def add_libname_arguments(parser):
    parser.add_argument('--topname', metavar='TOPNAME', dest='topname', type=str, default='Top', action=DeprecatedAction, replacement='-R',
                        help='The name to bind to the current directory using -R .')
    parser.add_argument('-R', metavar=('DIR', 'COQDIR'), dest='libnames', type=str, default=[], nargs=2, action=CoqLibnameAction,
                        help='recursively map physical DIR to logical COQDIR, as in the -R argument to coqc')
    parser.add_argument('-Q', metavar=('DIR', 'COQDIR'), dest='non_recursive_libnames', type=str, default=[], nargs=2, action=CoqLibnameAction,
                        help='(nonrecursively) map physical DIR to logical COQDIR, as in the -Q argument to coqc')
    parser.add_argument('-I', metavar='DIR', dest='ocaml_dirnames', type=str, default=[], action='append',
                        help='Look for ML files in DIR, as in the -I argument to coqc')
    parser.add_argument('--arg', metavar='ARG', dest='coq_args', type=str, action=ArgAppendWithWarningAction,
                        help='Arguments to pass to coqc and coqtop; e.g., " -indices-matter" (leading and trailing spaces are stripped)')
    parser.add_argument('-f', metavar='FILE', dest='CoqProjectFile', nargs=1, type=argparse.FileType('r'),
                        default=None,
                        help=("A _CoqProject file"))

def add_logging_arguments(parser):
    parser.add_argument('--verbose', '-v', dest='verbose',
                        action='count',
                        help='display some extra information')
    parser.add_argument('--quiet', '-q', dest='quiet',
                        action='count',
                        help='the inverse of --verbose')
    parser.add_argument('--log-file', '-l', dest='log_files', nargs='*', type=argparse.FileType('w'),
                        default=[sys.stderr],
                        help='The files to log output to.  Use - for stdout.')

def process_logging_arguments(args):
    if args.verbose is None: args.verbose = DEFAULT_VERBOSITY
    if args.quiet is None: args.quiet = 0
    args.log = make_logger(args.log_files)
    args.verbose -= args.quiet
    del args.quiet
    return args

def tokenize_CoqProject(contents):
    is_in_string = False
    cur = ''
    for ch in contents:
        if is_in_string:
            cur += ch
            if ch == '"':
                yield cur
                cur = ''
                is_in_string = False
        elif ch == '"':
            cur += ch
            is_in_string = True
        else:
            if ch in '\n\r\t ':
                if cur: yield cur
                cur = ''
            else:
                cur += ch
    if cur:
        yield cur

def argstring_to_iterable(arg):
    if arg[:1] == '"' and arg[-1:] == '"': arg = arg[1:-1]
    return arg.split(' ')

def append_coq_arg(env, arg):
    for key in ('coqc_args', 'coqtop_args', 'passing_coqc_args'):
        env[key] = tuple(list(env.get(key, [])) + list(argstring_to_iterable(arg)))

def process_CoqProject(env, contents):
    if contents is None: return
    tokens = tuple(tokenize_CoqProject(contents))
    i = 0
    while i < len(tokens):
        if tokens[i] == '-R' and i+2 < len(tokens):
            env['libnames'].append((tokens[i+1], tokens[i+2]))
            i += 3
        elif tokens[i] == '-Q' and i+2 < len(tokens):
            env['non_recursive_libnames'].append((tokens[i+1], tokens[i+2]))
            i += 3
        elif tokens[i] == '-I' and i+1 < len(tokens):
            env['ocaml_dirnames'].append(tokens[i+1])
            i += 2
        elif tokens[i] == '-arg' and i+1 < len(tokens):
            append_coq_arg(env, tokens[i+1])
            i += 2
        elif tokens[i][-2:] == '.v':
            env['_CoqProject_v_files'].append(tokens[i])
            i += 1
        else:
            if 'log' in env.keys(): env['log']('Unknown _CoqProject entry: %s' % repr(tokens[i]))
            env['_CoqProject_unknown'].append(tokens[i])
            i += 1

def update_env_with_libnames(env, args, default=(('.', 'Top'), )):
    env['libnames'] = []
    env['non_recursive_libnames'] = []
    env['ocaml_dirnames'] = []
    env['_CoqProject'] = None
    env['_CoqProject_v_files'] = []
    env['_CoqProject_unknown'] = []
    if args.CoqProjectFile:
        for f in args.CoqProjectFile:
            env['_CoqProject'] = f.read()
            f.close()
    process_CoqProject(env, env['_CoqProject'])
    env['libnames'].extend(args.libnames if len(args.libnames + args.non_recursive_libnames + env['libnames'] + env['non_recursive_libnames']) > 0 else list(default))
    env['non_recursive_libnames'].extend(args.non_recursive_libnames)
    env['ocaml_dirnames'].extend(args.ocaml_dirnames)


# http://stackoverflow.com/questions/5943249/python-argparse-and-controlling-overriding-the-exit-status-code
class ArgumentParser(argparse.ArgumentParser):
    def _get_action_from_name(self, name):
        """Given a name, get the Action instance registered with this parser.
        If only it were made available in the ArgumentError object. It is
        passed as it's first arg...
        """
        container = self._actions
        if name is None:
            return None
        for action in container:
            if '/'.join(action.option_strings) == name:
                return action
            elif action.metavar == name:
                return action
            elif action.dest == name:
                return action

    def error(self, message):
        def reraise(extra=''):
            super(ArgumentParser, self).error(message + extra)
        exc = sys.exc_info()[1]
        if exc:
            exc.argument = self._get_action_from_name(exc.argument_name)
            exc.reraise = reraise
            raise exc
        reraise()
