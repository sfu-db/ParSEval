
class PathChangeTracker:
    """
    Tracks changes in the symbolic path list during a context block.
    """
    def __init__(self, context):
        self.context = context
        lst = self.context.get('paths')
        self.start_length = len(lst)
    
    def __enter__(self):
        return self
    
    def get_delta(self):
        return self.context.get('paths')[self.start_length:]
        
    def __exit__(self,  exc_type, exc_value, traceback):
        pass
