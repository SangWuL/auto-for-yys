# -*- coding: utf-8 -*-
import os
import runpy
import sys


def _redirect_console_streams():
    null_stream = open(os.devnull, 'w', encoding='utf-8')
    if sys.stdout is None:
        sys.stdout = null_stream
    if sys.stderr is None:
        sys.stderr = null_stream


_redirect_console_streams()
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gui.py'),
               run_name='__main__')
