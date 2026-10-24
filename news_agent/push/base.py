class PushError(Exception):
    pass


class PushChannel:
    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg

    def send(self, title, content, **kwargs):
        raise NotImplementedError
