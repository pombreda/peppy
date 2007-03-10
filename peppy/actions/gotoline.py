# peppy Copyright (c) 2006-2007 Rob McMullen
# Licenced under the GPL; see http://www.flipturn.org/peppy for more info
"""
Includable file that is used to provide a Goto Line function for a
major mode.
"""

import os

import wx
import wx.stc as stc

from peppy.actions.minibuffer import *
from peppy.major import *
from peppy.debug import *


class GotoLine(MinibufferAction):
    """Goto a line number.
    
    Use minibuffer to request a line number, then go to that line in
    the stc.
    """

    name = "Goto Line..."
    tooltip = "Goto a line in the text."
    key_bindings = {'default': 'M-G',}
    minibuffer = IntMinibuffer
    minibuffer_label = "Goto Line:"

    def processMinibuffer(self, line):
        """
        Callback function used to set the stc to the correct line.
        """
        
        # stc counts lines from zero, but displayed starting at 1.
        dprint("goto line = %d" % line)
        mode = self.frame.getActiveMajorMode()
        mode.stc.GotoLine(line-1)
