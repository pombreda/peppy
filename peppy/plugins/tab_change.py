# peppy Copyright (c) 2006-2008 Rob McMullen
# Licenced under the GPLv2; see http://peppy.flipturn.org for more info
"""Tab Change actions

A group of actions used to change the contents of the current tab to something
else, or to adjust the tabs themselves.
"""

import os

import wx

from peppy.yapsy.plugins import *
from peppy.frame import *
from peppy.actions import *
from peppy.debug import *


class TabLeft(SelectAction):
    alias = "tab-left"
    name = "Change Focus to Tab on Left"
    tooltip = "Move the focus to the tab left of the current tab."
    default_menu = (("View/Tabs", -110), 100)
    key_bindings = {'default': "M-LEFT", }

    def action(self, index=-1, multiplier=1):
        self.frame.tabs.moveSelectionLeft()


class TabRight(SelectAction):
    alias = "tab-right"
    name = "Change Focus to Tab on Right"
    tooltip = "Move the focus to the tab right of the current tab."
    default_menu = ("View/Tabs", 110)
    key_bindings = {'default': "M-RIGHT", }

    def action(self, index=-1, multiplier=1):
        self.frame.tabs.moveSelectionRight()


class TabChangePlugin(IPeppyPlugin):
    """Yapsy plugin to register the tab change actions
    """
    def getActions(self):
        return [TabLeft, TabRight]