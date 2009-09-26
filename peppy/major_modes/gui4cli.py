# peppy Copyright (c) 2006-2009 Rob McMullen
# Licenced under the GPLv2; see http://peppy.flipturn.org for more info
"""Gui4Cli programming language editing support.

Major mode for editing Gui4Cli files.

Supporting actions and minor modes should go here only if they are uniquely
applicable to this major mode and can't be used in other major modes.  If
actions can be used with multiple major modes, they should be put in a
separate plugin in the peppy/plugins directory.
"""

import os

import wx
import wx.stc

from peppy.lib.foldexplorer import *
from peppy.lib.autoindent import *
from peppy.yapsy.plugins import *
from peppy.major import *
from peppy.fundamental import FundamentalMode

class Gui4CliMode(FundamentalMode):
    """Stub major mode for editing Gui4Cli files.

    This major mode has been automatically generated and is a boilerplate/
    placeholder major mode.  Enhancements to this mode are appreciated!
    """
    keyword = 'Gui4Cli'
    editra_synonym = 'Gui4Cli'
    stc_lexer_id = 58
    start_line_comment = u'//'
    end_line_comment = ''
    
    icon = 'icons/page_white.png'
    
    default_classprefs = (
        StrParam('extensions', 'gc gui', fullwidth=True),
       )


class Gui4CliModePlugin(IPeppyPlugin):
    """Plugin to register modes and user interface for Gui4Cli
    """
   
    def getMajorModes(self):
        yield Gui4CliMode
