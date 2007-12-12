# peppy Copyright (c) 2006-2007 Rob McMullen
# Licenced under the GPL; see http://www.flipturn.org/peppy for more info
"""
Adapter pattern that allows the usage of PyPE's search and replace
with peppy.

The point of the adapter pattern is to bolt up two different styles of
coding without interfering too much with either of them.  It is quite
possible to add on the methods that pype expects to MajorMode or
FundamentalMode, but they are designed differently enough that I want
to keep the differences separate.

As a bonus by using the PyPE plugins verbatim, I can grab any updates
made there without changing my code here, and perhaps any enhancements
I make could be transported to PyPE to benefit both projects.
"""

import os

import wx
import wx.stc

from peppy.actions.minibuffer import *

import peppy.pype.findbar as findbar


class PypeFindReplaceAdapterMixin(object):
    """
    Mixin object for both FindBar and ReplaceBar to map some functions
    that pype requests from the main pype object to our instance of
    a Minibuffer object.
    """
    
    def readPreferences(self):
        """
        Overrides pype's readPreferences in order to hook into the
        setup of the object.  This is called before the
        pype.findbar.ReplaceBar.setup routine, so we can use this hook
        to reset self.parent to our adapter object: FindMinibuffer.

        It's a bit tricky, and unfortunately I wasn't able to treat
        pype's findbar as a black box.  At __init__, self.parent
        points to the parent wx.Frame object and is used in the
        constructor for the wx.Panel that pype.findbar.ReplaceBar is
        based on, but after that, self.parent isn't used in that
        capacity.  So, we can point self.parent to our adapter object
        and then extend the adapter object to do what pype needs.
        """
        self.parent=self.root
        super(PypeFindReplaceAdapterMixin, self).readPreferences()

class PypeFindBar(PypeFindReplaceAdapterMixin, findbar.FindBar):
    """
    Peppy adapter for pype FindBar widget.  Pype uses an instanceof
    call to determine what to display (instead of just overriding the
    method that creates the widgets), so we have to subclass from
    FindBar.
    """
    pass

class PypeReplaceBar(PypeFindReplaceAdapterMixin, findbar.ReplaceBar):
    """
    Peppy adapter for pype ReplaceBar widget.  Pype uses an instanceof
    call to determine what to display (instead of just overriding the
    method that creates the widgets), so we have to subclass from
    ReplaceBar.
    """
    pass

class FindMinibuffer(Minibuffer):
    """
    Adapter for PyPE findbar.  Maps findbar callbacks to our stuff.
    """
    def createWindow(self):
        # Create the find bar widget.
        self.win=PypeFindBar(self.mode.wrapper, self)
        #print "findbar=%s" % self.win

    def focus(self):
        # When the focus is asked for by the minibuffer driver, set it
        # to the text ctrl or combo box of the pype findbar.
        self.win.box1.SetFocus()

    #### PyPE Compatability

    def getglobal(self, param):
        # Pype uses this to get some lists of previously accessed
        # searches.  For now, do nothing.
        return None

    def SetStatusText(self, text, log=None):
        # Set the statusbar on the major mode to the text requested by
        # the pype findbar.
        self.mode.frame.SetStatusText(text)

    def GetWindow1(self):
        # Returns the stc of the major mode.
        return self.mode.stc

    def Unsplit(self):
        # Pype's idiom for removing the widget from the screen.  It
        # expects the widget to be removed but not destroyed,
        # necessitating the detach_only parameter to removeMinibuffer
        self.mode.removeMinibuffer(detach_only=True)

    
class ReplaceMinibuffer(FindMinibuffer):
    """
    Adapter for PyPE replacebar.  Adds stuff specific to the replace
    features to the stuff already in the FindMinibuffer parent class.
    """
    def createWindow(self):
        self.win=PypeReplaceBar(self.mode.wrapper, self)
        self.control=self

    def GetPageCount(self):
        # Always force the check in ReentrantReplace that sets
        # the focus back to the page that is getting updated.
        return 1
    
    def GetCurrentPage(self):
        # Force the condition to be true in ReentrantReplace to set
        # the focus back to the text widget.
        return self
    

class FindText(MinibufferAction):
    name = "Find..."
    tooltip = "Search for a string in the text."
    key_bindings = {'default': "C-F", 'emacs': 'C-S', }
    minibuffer = FindMinibuffer

class ReplaceText(MinibufferAction):
    name = "Replace..."
    tooltip = "Replace a string in the text."
    key_bindings = {'emacs': 'F6', }
    minibuffer = ReplaceMinibuffer

