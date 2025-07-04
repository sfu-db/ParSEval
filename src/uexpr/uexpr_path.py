from typing import List, Union, Optional

class UExprPath:
    # cnt = 0
    '''
        UExpr Path is an uexpression leading to some specific position in the U-expression
    '''

    def __init__(self, parent, preds: Optional[List], index: Optional[int], _type: Optional[str]) -> None:
        self.inputs = None
        self.upreds: List = preds or []
        self.processed = False
        self.parent = parent
        self.children = []
        self.index = index
        self._type = _type

    def _branch_id(self, stack, branch):
        instrumentation_keywords = {"parseval", "uexpression"}
        for frame, filename, linenum, funcname, context, contextline in stack:
            if any(instrumentation_keyword in filename for instrumentation_keyword in instrumentation_keywords):
                continue
            return "{}:{}:{}".format(filename, linenum, branch)
        return None
    
    def get_length(self):
        if self.parent is None:
            return 0
        return 1 + self.parent.get_length()
    def find_child(self, pid):
        if self.index and self.index == pid:
            return self
        for c in self.children:
            exist = c.find_child(pid)
            if exist:
                return exist
        return None
    
    def add_child(self, index, predicates: List, _type):
        assert (self.find_child(index) is None)
        c = UExprPath(self, predicates, index, _type)
        self.children.append(c)
        return c

    def get_uexpr(self):
        if self.parent is None:
            return 1
        if self._type == 'filter':
            predicate = sum(self.upreds)
            predicate.set('t', None)
            return self.parent.get_uexpr() * predicate
        elif self._type == 'join':
            predicate = sum(self.upreds)
            return self.parent.get_uexpr() * predicate

    def __lt__(self, other):
        return self.get_length() > other.get_length()

    def __str__(self):
        return str(self.upreds) + "  (processed: %s, path_len: %d)" % (self.processed, self.get_length())
    
    