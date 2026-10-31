# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

class PushError(Exception):
    pass


class PushChannel:
    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg

    def send(self, title, content, **kwargs):
        raise NotImplementedError
