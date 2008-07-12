# peppy Copyright (c) 2006-2008 Rob McMullen
# Licenced under the GPLv2; see http://peppy.flipturn.org for more info
import os, re

import wx
import wx.stc

from peppy.actions.base import *
from peppy.debug import *
from peppy.lib.textctrl_autocomplete import TextCtrlAutoComplete
from peppy.lib.iconstorage import *
from peppy.lib.controls import StatusBarButton

class MinibufferMixin(object):
    minibuffer = None
    minibuffer_label = None
    
    def getMinibufferLabel(self):
        return self.minibuffer_label
    
    def getInitialValueHook(self):
        """Get the initial value (if any)
        
        This hook is called immediately before the minibuffer is placed in
        the mode.  If overridden in a subclass, this should return a text
        representation of the string to place in the minibuffer.
        """
        return ""
    
    def showMinibuffer(self, mode):
        initial = self.getInitialValueHook()
        label = self.getMinibufferLabel()
        if isinstance(self.minibuffer, list):
            minibuffer = MultiMinibuffer(mode, self, label=label, initial=initial, multi=self.minibuffer)
        else:
            minibuffer = self.minibuffer(mode, self, label=label, initial=initial)
        #print minibuffer.win
        mode.setMinibuffer(minibuffer)

    def processMinibuffer(self, minibuffer, mode, text):
        assert self.dprint("processing %s" % text)


class MinibufferAction(MinibufferMixin, TextModificationAction):
    key_needs_focus = False
    
    def action(self, index=-1, multiplier=1):
        self.showMinibuffer(self.mode)


class MinibufferRepeatAction(MinibufferAction):
    """If an existing minibuffer is of the same type as the newly requested
    minibuffer, call L{Minibuffer.repeat} instead of creating a new instance
    of the minibuffer.
    """
    
    def action(self, index=-1, multiplier=1):
        minibuffer = self.mode.getMinibuffer()
        if minibuffer.__class__ == self.minibuffer:
            self.dprint("Using the same type of minibuffer!")
            minibuffer.repeat(self)
        else:
            self.dprint("Not using the same type of minibuffer.  Creating a new one.")
            MinibufferAction.action(self, index, multiplier)


class Minibuffer(debugmixin):
    """
    Base class for an action that is implemented using the minibuffer.
    Minibuffer is a concept from emacs where, instead of popping up a
    dialog box, uses the bottom of the screen as a small user input
    window.
    """
    label = "Input:"
    error = "Bad input."
    
    def __init__(self, mode, action, label=None, error=None, initial=None,
                 parent=None, finish_callback=None, next=None, **kwargs):
        self.win = None
        self.mode = mode
        self.action = action
        if error is not None:
            self.error = error
        if label is not None:
            self.label = label
        self.initial = initial
        self.next_focus = next
        self.finish_callback = finish_callback
        
        if parent:
            self.createWindow(parent)
            self.panel = self.win
        else:
            self.panel = wx.Panel(self.mode.wrapper, style=wx.NO_BORDER)
            sizer = wx.BoxSizer(wx.HORIZONTAL)
            self.createWindow(self.panel)
            sizer.Add(self.win, 1, wx.EXPAND)
            close = StatusBarButton(self.panel, -1, getIconBitmap("icons/cancel.png"), style=wx.NO_BORDER)
            close.Bind(wx.EVT_BUTTON, self.OnClose)
            sizer.Add(close, 0, wx.EXPAND)
            self.panel.SetSizer(sizer)
        
    def createWindow(self, parent):
        """
        Create a window that represents the minibuffer, and set
        self.win to that window.
        
        @param parent: parent window of minibuffer
        """
        raise NotImplementedError

    def bindEvents(self, next=None):
        """Set up all required event handlers.
        
        @param next: optional next control that is used to change focus to the
        next minibuffer in the list if this is contained in a MultiMinibuffer
        """
        pass

    def OnEnterNext(self, evt):
        if self.next_focus:
            self.next_focus.focus()

    def repeat(self, action):
        """Entry point used to reinitialize the minibuffer without creating
        a new instance.
        
        @param action: the L{SelectAction} that caused the repeat, which could
        be different than C{self.action} stored during the __init__ method.
        """
        raise NotImplementedError
    
    def getHelp(self):
        """Return the help string for this minibuffer"""
        return ""

    def show(self):
        """Show the main minibuffer panel"""
        self.panel.Show()
        self.focus()

    def focus(self):
        """
        Set the focus to the component in the menubar that should get
        the text focus.
        """
        assert self.dprint("focus!!!")
        self.win.SetFocus()
    
    def addToSizer(self, sizer):
        """Add the minibuffer panel to the given sizer"""
        sizer.Add(self.panel, 0, wx.EXPAND)
        
    def detachFromSizer(self, sizer):
        """Remove the minibuffer panel from the given sizer"""
        sizer.Detach(self.panel)
    
    def OnClose(self, evt):
        """Callback handler to remove minibuffer from major mode"""
        wx.CallAfter(self.mode.removeMinibuffer)
    
    def closePreHook(self):
        """Hook called when minibuffer is about to be closed to allow it to
        save any persistent data.
        """
        pass
    
    def close(self):
        """
        Destroy the minibuffer widgets.
        """
        self.closePreHook()
        self.panel.Destroy()
        self.panel = None

    def removeFromParent(self, call_after=False):
        """
        Convenience routine to destroy minibuffer after the event loop
        exits.
        """
        # It's possible that the minibuffer has already been destroyed; for
        # example, when the minibuffer is used in an action that replaces
        # the mode in the current tab with another mode.  So, only attempt to
        # remove the minibuffer if the mode is still valid.
        if self.mode:
            if call_after:
                wx.CallAfter(self.mode.removeMinibuffer, self)
            else:
                self.mode.removeMinibuffer(self)
    
    def performAction(self, value):
        """Execute the processMinibuffer method of the action"""
        error = self.action.processMinibuffer(self, self.mode, value)
        if error is not None:
            self.mode.frame.SetStatusText(error)



class TextMinibuffer(Minibuffer):
    """
    Dedicated subclass of Minibuffer that prompts for a text string
    """
    label = "Text"
    error = "Bad input."
    
    def createWindow(self, parent):
        self.win = wx.Panel(parent, style=wx.NO_BORDER|wx.TAB_TRAVERSAL)
        
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        prompt = wx.StaticText(self.win, -1, _(self.label))
        sizer.Add(prompt, 0, wx.CENTER)
        self.text = wx.TextCtrl(self.win, -1, size=(-1,-1), style=wx.TE_PROCESS_ENTER)
        sizer.Add(self.text, 1, wx.EXPAND)
        self.win.SetSizer(sizer)

        self.text.Bind(wx.EVT_TEXT_ENTER, self.OnEnter)

        if self.initial:
            self.text.ChangeValue(self.initial)

    def convert(self, text):
        return text
    
    def getResult(self, show_error=True):
        text = self.text.GetValue()
        error = None
        assert self.dprint("text=%s" % text)
        try:
            text = self.convert(text)
        except:
            error = self.error
            if show_error:
                self.mode.frame.SetStatusText(error)
            text = None
        return text, error
    
    def OnEnter(self, evt):
        text, error = self.getResult()

        if self.next_focus:
            self.next_focus.focus()
        elif self.finish_callback:
            self.finish_callback()
        else:
            # Remove the minibuffer and perform the action in CallAfters so
            # the tab focus doesn't get confused.  If you try to perform these
            # actions directly, the focus will return to the original tab if
            # the action causes a new tab to be created.  Moving everything
            # to CallAfters prevents this.  Also, MSW can crash if performing
            # these directly, so it's worth the extra milliseconds to use
            # CallAfter
            wx.CallAfter(self.removeFromParent)
            if text is not None:
                wx.CallAfter(self.performAction, text)


class IntMinibuffer(TextMinibuffer):
    """Dedicated subclass of Minibuffer that prompts for an integer.
    
    Can handle python expressions, with the enhancement that it recognizez
    hex numbers in the msw format of abcd1234h; i.e.  with a 'h' after the
    hex digits.
    """
    label = "Integer"
    error = "Not an integer expression."
    
    # Regular expression that matches MSW hex format
    msw_hex = re.compile("[0-9a-fA-F]+h")

    def convert(self, text):
        # replace each occurrence of a MSW-style hex number to 0x style so that
        # eval can parse it.
        text = self.msw_hex.sub(lambda s: "0x%s" % s.group(0)[:-1], text)
        number = int(eval(text))
        assert self.dprint("number=%s" % number)
        return number

class IntRangeMinibuffer(IntMinibuffer):
    """Dedicated subclass of Minibuffer that prompts for a pair of integers.
    
    Can handle python expressions, with the enhancement that it recognizez
    hex numbers in the msw format of abcd1234h; i.e.  with a 'h' after the
    hex digits.
    """
    label = "Range"
    error = "Invalid range."

    def convert(self, text):
        # replace each occurrence of a MSW-style hex number to 0x style so that
        # eval can parse it.
        pair = []
        for val in text.split(','):
            pair.append(IntMinibuffer.convert(self, val))
        if len(pair) == 2:
            assert self.dprint("range=%s" % str(pair))
            return pair
        raise ValueError("Didn't specify a range")

class FloatMinibuffer(TextMinibuffer):
    """
    Dedicated subclass of Minibuffer that prompts for a floating point
    number.
    """
    label = "Floating Point"
    error = "Not a numeric expression."
    
    def convert(self, text):
        number = float(eval(self.text.GetValue()))
        assert self.dprint("number=%s" % number)
        return number


class InPlaceCompletionMinibuffer(TextMinibuffer):
    """Base class for a simple autocompletion minibuffer.

    This completion style is like Outlook's email address completion
    where it suggests the best completion ahead of the cursor,
    adjusting as you type.  There is no dropdown list; everything is
    handled in the text ctrl.

    This class doesn't implement the complete method, leaving its
    implementation to subclasses.
    """
    def createWindow(self, parent):
        self.win = wx.Panel(parent, style=wx.NO_BORDER|wx.TAB_TRAVERSAL)
        
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        prompt = wx.StaticText(self.win, -1, _(self.label))
        sizer.Add(prompt, 0, wx.CENTER)
        self.text = wx.TextCtrl(self.win, -1, size=(-1,-1), style=wx.TE_PROCESS_ENTER|wx.TE_PROCESS_TAB)
        sizer.Add(self.text, 1, wx.EXPAND)
        self.win.SetSizer(sizer)

        self.text.Bind(wx.EVT_TEXT_ENTER, self.OnEnter)
        self.text.Bind(wx.EVT_TEXT, self.OnText)
        self.text.Bind(wx.EVT_KEY_DOWN, self.OnKeyDown)

        if self.initial:
            self.text.ChangeValue(self.initial)

        self.win.saveSetFocus = self.win.SetFocus
        self.win.SetFocus = self.SetFocus
        
    def SetFocus(self):
        dprint(self)
        self.win.saveSetFocus()
        self.text.SetInsertionPointEnd()

    def OnText(self, evt):
        text = evt.GetString()
        dprint(text)
        evt.Skip()

    def complete(self, text):
        """Generate the completion list.

        The best guess should be returned as the first item in the
        list, with each subsequent entry being less probable.
        """
        raise NotImplementedError

    def processCompletion(self, text):
        guesses = self.complete(text)
        if guesses:
            self.text.SetValue(guesses[0])
            self.text.SetSelection(len(text), -1)

    def OnKeyDown(self, evt):
        key = evt.GetKeyCode()
        #dprint(key)
        if key == wx.WXK_TAB:
            self.processCompletion(self.text.GetValue())
            # don't call Skip() here.  That way wx knows not to
            # continue on to the EVT_TEXT callback
            return
        evt.Skip()

class CompletionMinibuffer(TextMinibuffer):
    """Base class for a minibuffer based on the TextCtrlAutoComplete
    widget from the wxpython list.

    This class doesn't implement the complete method, leaving its
    implementation to subclasses.
    """
    def createWindow(self, parent):
        self.win = wx.Panel(parent, style=wx.NO_BORDER|wx.TAB_TRAVERSAL)
        
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        prompt = wx.StaticText(self.win, -1, _(self.label))
        sizer.Add(prompt, 0, wx.CENTER)
        self.text = TextCtrlAutoComplete(self.win, choices=[], size=(-1,-1), style=wx.TE_PROCESS_ENTER|wx.TE_PROCESS_TAB)
        sizer.Add(self.text, 1, wx.EXPAND)
        self.win.SetSizer(sizer)

        self.text.Bind(wx.EVT_TEXT_ENTER, self.OnEnter)
        self.win.Bind(wx.EVT_SET_FOCUS, self.OnFocus)

        if self.initial is not None:
            self.text.ChangeValue(self.initial)
            self.text.SetChoices(self.complete(self.initial))
        self.text.SetEntryCallback(self.setDynamicChoices)
        #self.text.SetInsertionPointEnd()

        # FIXME: Using the EVT_SET_FOCUS doesn't seem to work to set the cursor
        # to the end of the text.  It doesn't seem to get called at all, so
        # the only way to do it appears to be to co-opt the Panel's SetFocus
        # method
        self.win.saveSetFocus = self.win.SetFocus
        self.win.SetFocus = self.SetFocus
        
    def SetFocus(self):
        #dprint(self)
        self.win.saveSetFocus()
        self.text.SetInsertionPointEnd()
    
    def OnFocus(self, evt):
        #dprint()
        self.text.SetInsertionPointEnd()

    def complete(self, text):
        raise NotImplementedError
        
    def setDynamicChoices(self):
        ctrl = self.text
        text = ctrl.GetValue()
        current_choices = ctrl.GetChoices()
        choices = self.complete(text)
        self.dprint(choices)
        if choices != current_choices:
            ctrl.SetChoices(choices)

class StaticListCompletionMinibuffer(CompletionMinibuffer):
    """Completion minibuffer where the list of possibilities doesn't change.

    This is used to complete on a static list of items.  This doesn't
    handle cases like searching through the filesystem where a new
    list of matches is generated when you hit a new directory.
    """
    def __init__(self, *args, **kwargs):
        if 'list' in kwargs:
            self.sorted = kwargs['list']
        else:
            self.sorted = []
        CompletionMinibuffer.__init__(self, *args, **kwargs)
        
    def complete(self, text):
        """Return the list of completions that start with the given text"""
        found = []
        for match in self.sorted:
            if match.find(text) >= 0:
                found.append(match)
        return found


class MultiMinibuffer(Minibuffer):
    """Base class for a composite minibuffer.
    
    This provides multiple minibuffer items wrapped in the same minibuffer.
    The minibuffers can be different types and have different labels; a
    multi minibuffer is specified by supplying a list for C{minibuffer} and
    C{minibuffer_label}, where C{minibuffer} supplies the list of L{Minibuffer}
    subclasses, and C{minibuffer_label} lists the labels for each.
    """
    def __init__(self, *args, **kwargs):
        labels = kwargs['label']
        multis = kwargs['multi']
        if labels is None:
            labels = [None] * len(multis)
        if len(labels) != len(multis):
            raise IndexError("Must have the same number of labels as minibuffers")
        self.multi_info = zip(multis, labels)
        self.minibuffers = []
        Minibuffer.__init__(self, *args, **kwargs)
        
    def createWindow(self, parent):
        self.win = wx.Panel(parent, style=wx.NO_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        last = None
        for minibuffer, label in self.multi_info:
            self.dprint(minibuffer)
            mb = minibuffer(self.mode, self.action, label, parent=self.panel, finish_callback=self.finished)
            mb.addToSizer(sizer)
            self.minibuffers.append(mb)
            if last:
                last.next_focus = mb
            last = mb
        
        self.win.SetSizer(sizer)
    
    def focus(self):
        self.minibuffers[0].focus()

    def finished(self):
        results = []
        for minibuffer in self.minibuffers:
            text, error = minibuffer.getResult()
            results.append(text)
            if error:
                #dprint(error)
                minibuffer.focus()
                return
        
        wx.CallAfter(self.removeFromParent)
        wx.CallAfter(self.performAction, results)
