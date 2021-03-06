import re, time
from subprocess import Popen, PIPE, STDOUT
import split_definitions_old
from split_file import postprocess_split_proof_term
from coq_version import get_coq_accepts_time, get_proof_term_works_with_time
from custom_arguments import DEFAULT_LOG, DEFAULT_VERBOSITY
import util

__all__ = ["join_definitions", "split_statements_to_definitions"]

def get_definitions_diff(previous_definition_string, new_definition_string):
    """Returns a triple of lists (definitions_removed,
    definitions_shared, definitions_added)"""
    old_definitions = [i for i in previous_definition_string.split('|') if i]
    new_definitions = [i for i in new_definition_string.split('|') if i]
    if all(i == 'branch' for i in old_definitions + new_definitions): # work
        # around bug #5577 when all theorem names are "branch", we
        # don't assume that names are unique, and instead go by
        # ordering
        removed = []
        shared = []
        added = []
        for i in range(max((len(old_definitions), len(new_definitions)))):
            if i < len(old_definitions) and i < len(new_definitions):
                if old_definitions[i] == new_definitions[i]:
                    shared.append(old_definitions[i])
                else:
                    removed.append(old_definitions[i])
                    added.append(new_definitions[i])
            elif i < len(old_definitions):
                removed.append(old_definitions[i])
            elif i < len(new_definitions):
                added.append(new_definitions[i])
        return (tuple(removed), tuple(shared), tuple(added))
    else:
        return (tuple(i for i in old_definitions if i not in new_definitions),
                tuple(i for i in old_definitions if i in new_definitions),
                tuple(i for i in new_definitions if i not in old_definitions))

def strip_newlines(string):
    if not string: return string
    if string[0] == '\n': return string[1:]
    if string[-1] == '\n': return string[:-1]
    return string

def split_statements_to_definitions(statements, verbose=DEFAULT_VERBOSITY, log=DEFAULT_LOG, coqtop='coqtop', coqtop_args=tuple(), **kwargs):
    """Splits a list of statements into chunks which make up
    independent definitions/hints/etc."""
    def fallback():
        if verbose: log("Your version of coqtop doesn't support -time.  Falling back to more error-prone method.")
        return split_definitions_old.split_statements_to_definitions(statements, verbose=verbose, log=log, coqtop=coqtop, coqtop_args=coqtop_args)
    # check for -time
    if not get_coq_accepts_time(coqtop, verbose=verbose, log=log):
        return fallback()
    if not get_proof_term_works_with_time(coqtop, is_coqtop=True, verbose=verbose, log=log, **kwargs):
        statements = postprocess_split_proof_term(statements, log=log, verbose=verbose, **kwargs)
    p = Popen([coqtop, '-q', '-emacs', '-time'] + list(coqtop_args), stdout=PIPE, stderr=STDOUT, stdin=PIPE)
    split_reg = re.compile(r'Chars ([0-9]+) - ([0-9]+) [^\s]+ (.*?)(?=Chars [0-9]+ - [0-9]+|$)'.replace(' ', r'\s*'),
                           flags=re.DOTALL)
    prompt_reg = re.compile(r'^(.*?)<prompt>([^<]*?) < ([0-9]+) ([^<]*?) ([0-9]+) < ([^<]*?)</prompt>'.replace(' ', r'\s*'),
                            flags=re.DOTALL)
    defined_reg = re.compile(r'^([^\s]+) is (?:defined|assumed)$', re.MULTILINE)
    # goals and definitions are on stdout, prompts are on stderr
    statements_string = '\n'.join(statements) + '\n\n'
    statements_bytes = statements_string.encode('utf-8')
    if verbose: log('Sending statements to coqtop...')
    if verbose >= 3: log(statements_string)
    (stdout, stderr) = p.communicate(input=statements_bytes)
    stdout = util.s(stdout)
    if 'know what to do with -time' in stdout.strip().split('\n')[0]:
        # we're using a version of coqtop that doesn't support -time
        return fallback()
    if verbose: log('Done.  Splitting to definitions...')

    rtn = []
    cur_definition = {}
    last_definitions = '||'
    cur_definition_names = '||'
    last_char_end = 0

    #if verbose >= 3: log('re.findall(' + repr(r'Chars ([0-9]+) - ([0-9]+) [^\s]+ (.*?)<prompt>([^<]*?) < ([0-9]+) ([^<]*?) ([0-9]+) < ([^<]*?)</prompt>'.replace(' ', r'\s*')) + ', ' + repr(stdout) + ', ' + 'flags=re.DOTALL)')
    responses = split_reg.findall(stdout)
    for char_start, char_end, full_response_text in responses:
        char_start, char_end = int(char_start), int(char_end)
        # if we've travelled backwards in time, as in
        # COQBUG(https://github.com/coq/coq/issues/14475); we just
        # ignore this statement
        if char_end <= last_char_end: continue
        match = prompt_reg.match(full_response_text)
        if not match:
            log('Warning: Could not find statements in %d:%d: %s' % (char_start, char_end, full_response_text))
            continue
        response_text, cur_name, line_num1, cur_definition_names, line_num2, unknown = match.groups()
        statement = strip_newlines(statements_bytes[last_char_end:char_end].decode('utf-8'))
        last_char_end = char_end

        terms_defined = defined_reg.findall(response_text)

        definitions_removed, definitions_shared, definitions_added = get_definitions_diff(last_definitions, cur_definition_names)

        # first, to be on the safe side, we add the new
        # definitions key to the dict, if it wasn't already there.
        if cur_definition_names.strip('|') and cur_definition_names not in cur_definition:
            cur_definition[cur_definition_names] = {'statements':[], 'terms_defined':[]}


        if verbose >= 2: log((statement, (char_start, char_end), definitions_removed, terms_defined, 'last_definitions:', last_definitions, 'cur_definition_names:', cur_definition_names, cur_definition.get(last_definitions, []), cur_definition.get(cur_definition_names, []), response_text))


        # first, we handle the case where we have just finished
        # defining something.  This should correspond to
        # len(definitions_removed) > 0 and len(terms_defined) > 0.
        # If only len(definitions_removed) > 0, then we have
        # aborted something.  If only len(terms_defined) > 0, then
        # we have defined something with a one-liner.
        if definitions_removed:
            cur_definition[last_definitions]['statements'].append(statement)
            cur_definition[last_definitions]['terms_defined'] += terms_defined
            if cur_definition_names.strip('|'):
                # we are still inside a definition.  For now, we
                # flatten all definitions.
                #
                # TODO(jgross): Come up with a better story for
                # nested definitions.
                cur_definition[cur_definition_names]['statements'] += cur_definition[last_definitions]['statements']
                cur_definition[cur_definition_names]['terms_defined'] += cur_definition[last_definitions]['terms_defined']
                del cur_definition[last_definitions]
            else:
                # we're at top-level, so add this as a new
                # definition
                rtn.append({'statements':tuple(cur_definition[last_definitions]['statements']),
                            'statement':'\n'.join(cur_definition[last_definitions]['statements']),
                            'terms_defined':tuple(cur_definition[last_definitions]['terms_defined'])})
                del cur_definition[last_definitions]
                # print('Adding:')
                # print(rtn[-1])
        elif terms_defined:
            if cur_definition_names.strip('|'):
                # we are still inside a definition.  For now, we
                # flatten all definitions.
                #
                # TODO(jgross): Come up with a better story for
                # nested definitions.
                cur_definition[cur_definition_names]['statements'].append(statement)
                cur_definition[cur_definition_names]['terms_defined'] += terms_defined
            else:
                # we're at top level, so add this as a new
                # definition
                rtn.append({'statements':(statement,),
                            'statement':statement,
                            'terms_defined':tuple(terms_defined)})

        # now we handle the case where we have just opened a fresh
        # definition.  We've already added the key to the
        # dictionary.
        elif definitions_added:
            # print(definitions_added)
            cur_definition[cur_definition_names]['statements'].append(statement)
        else:
            # if we're in a definition, append the statement to
            # the queue, otherwise, just add it as it's own
            # statement
            if cur_definition_names.strip('|'):
                cur_definition[cur_definition_names]['statements'].append(statement)
            else:
                rtn.append({'statements':(statement,),
                            'statement':statement,
                            'terms_defined':tuple()})

        last_definitions = cur_definition_names

    if verbose >= 2: log((last_definitions, cur_definition_names))
    if last_definitions.strip('||'):
        rtn.append({'statements':tuple(cur_definition[cur_definition_names]['statements']),
                    'statement':'\n'.join(cur_definition[cur_definition_names]['statements']),
                    'terms_defined':tuple(cur_definition[cur_definition_names]['terms_defined'])})
        del cur_definition[last_definitions]

    if last_char_end + 1 < len(statements_bytes):
        last_statement = statements_bytes[last_char_end:].decode('utf-8')
        if verbose >= 2: log('Appending end of code from %d to %d: %s' % (last_char_end, len(statements_bytes), last_statement))
        last_statement = strip_newlines(last_statement)
        rtn.append({'statements':tuple(last_statement,),
                    'statement':last_statement,
                    'terms_defined':tuple()})

    return rtn

def join_definitions(definitions):
    return '\n'.join(i['statement'] for i in definitions)
