"""A light structural reader for Ruby source (no Ruby needed).

It blanks string contents and strips comments and heredocs, joins continuation lines into logical
statements, and matches every `end` with its opener (class, module, def, do, begin, and if, unless,
case, while, until at the start of a statement or after an assignment).  The result is a tree of
Nodes (class, module, sclass, def, block, ctrl) whose children are Stmts and Nodes.  Rails code is
formatted by RuboCop, which keeps this reliable; files that do not balance are reported.
"""
import re


class Stmt:
    __slots__ = ('text', 'code', 'line')

    def __init__(self, text, code, line):
        self.text, self.code, self.line = text, code, line

    def __repr__(self):
        return 'Stmt(%d: %s)' % (self.line, self.text[:60])


class Node:
    __slots__ = ('kind', 'name', 'head', 'line', 'children', 'parent', 'end')

    def __init__(self, kind, name, head, line, parent):
        self.kind, self.name, self.head, self.line, self.parent = kind, name, head, line, parent
        self.children = []
        self.end = line

    def walk(self):
        for c in self.children:
            yield c
            if isinstance(c, Node):
                yield from c.walk()

    def stmts(self):
        """Every statement inside, including the heads of nested blocks."""
        for c in self.walk():
            if isinstance(c, Stmt):
                yield c
            elif c.head is not None:
                yield c.head

    def path(self):
        out, n = [], self
        while n is not None and n.kind != 'file':
            if n.kind in ('class', 'module'):
                out.append(n.name)
            n = n.parent
        return '::'.join(reversed(out))

    def __repr__(self):
        return 'Node(%s %s @%d)' % (self.kind, self.name, self.line)


_HEREDOC = re.compile(r'<<([~-]?)(["\'`]?)([A-Z_][A-Z0-9_]*)\2')


_PAIR = {'(': ')', '[': ']', '{': '}', '<': '>'}
_REGEX_PREV = set('(,=~!|&{[;:?+-*<>^%')
_REGEX_KW = re.compile(r'(?:^|[^\w.])(?:when|if|unless|and|or|not|return|elsif|while|until|in|then|puts|p)\s*$')


def _regex_start(line, i):
    """Is the '/' at position i the start of a regex literal?"""
    j = i - 1
    while j >= 0 and line[j] == ' ':
        j -= 1
    if j < 0:
        return True
    if line[j] in _REGEX_PREV:
        return True
    if _REGEX_KW.search(line[:j + 1]):
        return True
    # method call with a space before a regex argument: `split /,/` but not `a / b`
    if j < i - 1 and line[j].isalnum() and i + 1 < len(line) and line[i + 1] not in ' =':
        return bool(re.search(r'(?:^|[\s.(])[a-z_]\w*[?!]?$', line[:j + 1]))
    return False


def blank_line(line, state):
    """Return (code, text) with comments removed and literal contents replaced by '_' in code.
    state is a stack carried across lines; a literal frame is ['lit', closer, opener, nesting,
    is_regex, interpolates] and an interpolation frame (code inside #{...}) is ['int', braces]."""
    code, text = [], []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        top = state[-1] if state else None
        if top is not None and top[0] == 'lit':
            _, closer, opener, nest, rx, interp = top
            if ch == '\\' and i + 1 < n:
                code.append('__')
                text.append(line[i:i + 2])
                i += 2
                continue
            if interp and ch == '#' and i + 1 < n and line[i + 1] == '{':
                state.append(['int', 0])
                code.append('__')
                text.append('#{')
                i += 2
                continue
            if opener and ch == opener:
                top[3] += 1
            elif ch == closer:
                if nest:
                    top[3] -= 1
                else:
                    state.pop()
                    code.append(ch if not state else '_')
                    text.append(ch)
                    i += 1
                    if rx:
                        while i < n and line[i] in 'imxounse':
                            code.append(line[i] if not state else '_')
                            text.append(line[i])
                            i += 1
                    continue
            code.append('_')
            text.append(ch)
            i += 1
            continue
        inside = top is not None          # code inside an interpolation
        out = (lambda c: '_' * len(c)) if inside else (lambda c: c)
        if inside:
            if ch == '{':
                top[1] += 1
            elif ch == '}':
                if top[1] == 0:
                    state.pop()
                    code.append('_')
                    text.append(ch)
                    i += 1
                    continue
                top[1] -= 1
        elif ch == '#':
            break
        if ch in '"\'`':
            state.append(['lit', ch, None, 0, False, ch != "'"])
            code.append(out(ch))
            text.append(ch)
            i += 1
            continue
        if ch == '%' and i + 1 < n and (i == 0 or not (line[i - 1].isalnum() or line[i - 1] in ')]}_')):
            kind = None
            if line[i + 1] in 'qQwWiIrsx' and i + 2 < n and not line[i + 2].isalnum() and line[i + 2] not in ' =':
                kind, op, k = line[i + 1], line[i + 2], i + 3
            elif line[i + 1] in '([{<|!/':
                kind, op, k = 'Q', line[i + 1], i + 2
            if kind:
                state.append(['lit', _PAIR.get(op, op), op if op in _PAIR else None, 0, kind == 'r',
                              kind in 'QWIrx'])
                code.append(out(line[i:k]))
                text.append(line[i:k])
                i = k
                continue
        if ch == '/' and _regex_start(line, i):
            state.append(['lit', '/', None, 0, True, True])
            code.append(out(ch))
            text.append(ch)
            i += 1
            continue
        if ch == '?' and i + 1 < n and line[i + 1] in '"\'' and (i == 0 or not line[i - 1].isalnum()):
            code.append(out('?_'))
            text.append(line[i:i + 2])
            i += 2
            continue
        code.append(out(ch))
        text.append(ch)
        i += 1
    return ''.join(code).rstrip(), ''.join(text).rstrip()


_CONT_END = re.compile(r'(,|\(|\[|\{|\\|&&|\|\||\band\b|\bor\b|[+\-*/%<>=!&|^~]|\.|\bnot\b|:)\s*$')
_SYMBOL_END = re.compile(r'(?<![:\w]):\w+\s*$|\w[?!]\s*$')


def logical_statements(src):
    """Yield (line_no, text, code) for every logical statement."""
    lines = src.split('\n')
    state = []
    heredocs = []
    buf_t, buf_c, start = [], [], 0
    depth = 0
    in_doc = False
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if in_doc:
            if raw.startswith('=end'):
                in_doc = False
            continue
        if raw.startswith('=begin'):
            in_doc = True
            continue
        if raw.strip() == '__END__':
            break
        if heredocs:
            term = heredocs[0]
            if raw.strip() == term:
                heredocs.pop(0)
            continue
        code, text = blank_line(raw, state)
        for m in _HEREDOC.finditer(code if not state else ''):
            heredocs.append(m.group(3))
        if not code.strip() and not buf_c:
            continue
        if not buf_c:
            start = i
        buf_c.append(code.strip())
        buf_t.append(text.strip())
        for ch in code:
            if ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
        joined = ' '.join(buf_c)
        cont = state or depth > 0 or bool(heredocs and False)
        if not cont:
            s = code.rstrip()
            if s and _CONT_END.search(s) and not _SYMBOL_END.search(s) and not s.endswith('->') \
                    and not re.search(r'\|\s*$', s) and not re.search(r'\bdo\s*$', s):
                cont = True
            nxt = lines[i].lstrip() if i < len(lines) else ''
            if nxt.startswith('.') and not nxt.startswith('..') or nxt.startswith('&.'):
                cont = True
        if cont and i < len(lines):
            continue
        yield start, ' '.join(buf_t), joined, list(buf_c)
        buf_t, buf_c = [], []
        depth = 0
        state.clear()


_OPEN_LEAD = re.compile(
    r'^(?:(?:private|protected|public|module_function|memoize|override|private_class_method|'
    r'public_class_method)\s+)*(class|module|def|begin|case|if|unless|while|until|for)\b(?![?!]|:(?!:))')
_OPEN_MID = re.compile(r'(?:[=(]|\|\|=|&&=)\s*(if|unless|case|begin|while|until)\b(?![?!]|:(?!:))')
_DO = re.compile(r'(?<![.:\w])do\b(?![?!:])')
_END = re.compile(r'(?<![.:\w@$])end\b(?![?!:]|\s*:)')
_ENDLESS_DEF = re.compile(r'^(?:\w+\s+)*def\s+(self\.)?[\w?!]+(?:\([^)]*\)\s*|\s+)=(?![=~>])\s*\S')


def _events(code):
    """Openers and ends of one statement, in order: list of (pos, kind)."""
    ev = []
    m = _OPEN_LEAD.match(code)
    lead_loop = False
    if m:
        kw = m.group(1)
        if kw == 'def' and _ENDLESS_DEF.match(code):
            pass
        elif kw == 'class' and re.match(r'.*\bclass\s*<<', code):
            ev.append((m.start(1), 'sclass'))
        else:
            ev.append((m.start(1), kw))
            lead_loop = kw in ('while', 'until', 'for')
    for m in _OPEN_MID.finditer(code):
        ev.append((m.start(1), m.group(1)))
    for m in _DO.finditer(code):
        if lead_loop and not any(k == 'do' for _, k in ev):
            lead_loop = False
            continue
        ev.append((m.start(), 'do'))
    for m in _END.finditer(code):
        ev.append((m.start(), 'end'))
    ev.sort()
    return ev


_KIND = {'class': 'class', 'module': 'module', 'sclass': 'sclass', 'def': 'def', 'do': 'block'}


def parse(src):
    """Parse Ruby source into a Node tree; returns (root, balanced)."""
    root = Node('file', '', None, 0, None)
    cur = root
    ok = True
    for line, text, code, parts in logical_statements(src):
        st = Stmt(text, code, line)
        ev = [e for part in parts for e in _events(part)]
        attached = False
        for pos, kind in ev:
            if kind == 'end':
                if cur.parent is None:
                    ok = False
                    continue
                cur.end = line
                cur = cur.parent
                continue
            name = ''
            if kind in ('class', 'module'):
                mm = re.match(r'.*?\b(?:class|module)\s+([\w:]+)', text)
                name = mm.group(1) if mm else ''
            elif kind == 'def':
                mm = re.match(r'.*?\bdef\s+((?:self\.)?[\w?!=\[\]+\-*/<>~%]+)', text)
                name = mm.group(1) if mm else ''
            node = Node(_KIND.get(kind, 'ctrl'), name, st, line, cur)
            cur.children.append(node)
            attached = True
            cur = node
        if not attached:
            cur.children.append(st)
    if cur is not root:
        ok = False
    return root, ok


def read(path):
    import fsutil
    return parse(fsutil.read(path))
