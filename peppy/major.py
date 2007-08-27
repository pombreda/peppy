# peppy Copyright (c) 2006-2007 Rob McMullen
# Licenced under the GPL; see http://www.flipturn.org/peppy for more info
"""Base module for major mode implementations.

Implementing a new major mode means to extend from the L{MajorMode}
base class and at least implement the createEditWindow method.
Several attributes must be set: C{icon} that points to the filename of
the icon to be used, and C{keyword} which is a text string that
uniquely identifies the major mode from other major modes.  [FIXME:
more documentation here.]

Once the major mode subclass has been created, it must be announced to
peppy.  This is done by creating another class that extends from
L{MajorModeMatcherBase} and implements the L{IMajorModeMatcher}
interface.  This interface is used by the file loader to determine
which major mode gets the default view of a file when it is opened.

Because MajorModeMatcherBase is a trac component, all you have to do
is list your new module in the plugin path specified in your
configuration directory, and it will get picked up the next time peppy
starts.  You can also place them in your python's [FIXME: some
directory to be added like site-packages/peppy/autoload] directory
which is always scanned by peppy as it starts.

To provide user interface objects, you can add the
L{IMenuItemProvider} and L{IToolBarItemProvider} interfaces to your
subclass of L{IMajorModeMatcher} and implement the methods that those
require to add menu items or toolbar items.  Note that the first
element of the tuple returned by getMenuItems or getToolBarItems will
be the C{keyword} attribute of your major mode.
"""

import os,sys,re

import wx
import wx.stc
from wx.lib.pubsub import Publisher

from peppy import *
from menu import *
from stcinterface import *
from configprefs import *
from debug import *
from minor import *
from iofilter import *

from lib.iconstorage import *
from lib.controls import *

class BufferBusyActionMixin(object):
    """Mixin to disable an action when the buffer is being modified.

    If a subclass needs to supply more information about its enable
    state, override isActionAvailable instead of isEnabled, or else
    you lose the buffer busy test.
    """
    def isEnabled(self):
        mode = self.frame.getActiveMajorMode()
        if mode is not None:
            return not mode.buffer.busy and self.isActionAvailable(mode)
        return False

    def isActionAvailable(self, mode):
        return True

class BufferModificationAction(BufferBusyActionMixin, SelectAction):
    """Base class for any action that changes the bytes in the buffer.

    This uses the BufferBusyActionMixin to disable any action that
    would change the buffer when the buffer is in the process of being
    modified by a long-running process.
    """
    # Set up a new run() method to pass the viewer
    def action(self, pos=-1):
        assert self.dprint("id=%s name=%s" % (id(self),self.name))
        self.modify(self.frame.getActiveMajorMode(),pos)

    def modify(self, mode, pos=-1):
        raise NotImplementedError

    # Set up a new keyboard callback to also pass the viewer
    def __call__(self, evt, number=None):
        assert self.dprint("%s called by keybindings" % self)
        self.modify(self.frame.getActiveMajorMode())


class MajorModeSelect(BufferBusyActionMixin, RadioAction):
    name="Major Mode"
    inline=False
    tooltip="Switch major mode"

    modes=None
    items=None

    def initPreHook(self):
        currentmode = self.frame.getActiveMajorMode()
        modes = self.frame.app.getSubclasses(MajorMode)

        # Only display those modes that use the same type of STC as
        # the current mode.
        modes = [m for m in modes if m.mmap_stc_class == currentmode.mmap_stc_class]
        
        modes.sort(key=lambda s:s.keyword)
        assert self.dprint(modes)
        MajorModeSelect.modes = modes
        names = [m.keyword for m in modes]
        MajorModeSelect.items = names

    def saveIndex(self,index):
        assert self.dprint("index=%d" % index)

    def getIndex(self):
        modecls = self.frame.getActiveMajorMode().__class__
        assert self.dprint("searching for %s in %s" % (modecls, MajorModeSelect.modes))
        if modecls is not None.__class__:
            return MajorModeSelect.modes.index(modecls)
        return 0
                                           
    def getItems(self):
        return MajorModeSelect.items

    def action(self, index=0, old=-1):
        self.frame.changeMajorMode(MajorModeSelect.modes[index])


#### MajorMode base class

class MajorMode(wx.Panel,debugmixin,ClassSettings):
    """
    Base class for all major modes.  Subclasses need to implement at
    least createEditWindow that will return the main user interaction
    window.
    """
    debuglevel = 0
    
    icon = 'icons/page_white.png'
    keyword = 'Abstract Major Mode'
    regex = None
    temporary = False # True if it is a temporary view

    # mmap_stc_class is used to associate this major mode with a
    # storage mechanism (implementing the STCInterface) that allows
    # files larger than can reside in memory.  Specifying a class here
    # implies that this major mode is not using a subclass of the
    # scintilla editor
    mmap_stc_class = None

    default_settings = {
        'line_number_offset': 1,
        'column_number_offset': 1,
        }

    # Need one keymap per subclass, so we can't use the settings.
    # Settings would propogate up the class hierachy and find a keymap
    # of a superclass.  This is a dict based on the class name.
    localkeymaps = {}
    
    def __init__(self,buffer,frame):
        self.splitter=None
        self.editwin=None # user interface window
        self.minibuffer=None
        self.sidebar=None
        self.stc=None # data store
        self.buffer=buffer
        self.frame=frame
        self.popup=None
        self.minors=[]
        self.statusbar = None
        
        wx.Panel.__init__(self, frame.tabs, -1, style=wx.NO_BORDER)
        self.createWindow()
        self.createWindowPostHook()
        self.createEventBindings()
        self.createEventBindingsPostHook()
        self.createListeners()
        self.createListenersPostHook()

    def __del__(self):
        dprint("deleting %s: buffer=%s" % (self.__class__.__name__,self.buffer))
        dprint("deleting %s: %s" % (self.__class__.__name__,self.getTabName()))
        self.removeListeners()
        self.removeListenersPostHook()
        self.deleteWindowPostHook()

    @classmethod
    def openSpecialNonFileHook(cls, url, fh):
        """Hook to short-circuit opening process if the major mode doesn't
        use traditional file-like objects.
        
        To handle a major mode that doesn't use file-like objects, intercept
        the opening process here by returning True from this method.
        """ 
        return None

    # If there is no title, return the keyword
    def getTitle(self):
        return self.keyword

    def getTabName(self):
        return self.buffer.getTabName()

    def getIcon(self):
        return getIconStorage(self.icon)

    def getWelcomeMessage(self):
        return "%s major mode" % self.keyword
    
    def createWindow(self):
        box=wx.BoxSizer(wx.VERTICAL)
        self.SetAutoLayout(True)
        self.SetSizer(box)
        self.splitter=wx.Panel(self)
        box.Add(self.splitter,1,wx.EXPAND)
        self._mgr = wx.aui.AuiManager()
        self._mgr.SetManagedWindow(self.splitter)
        self.editwin=self.createEditWindow(self.splitter)
        self._mgr.AddPane(self.editwin, wx.aui.AuiPaneInfo().Name("main").
                          CenterPane())

        self.loadMinorModes()
        
        self._mgr.Update()

    def createWindowPostHook(self):
        pass

    def deleteWindow(self):
        # remove reference to this view in the buffer's listeners
        assert self.dprint("closing view %s of buffer %s" % (self,self.buffer))
        self.buffer.remove(self)
        assert self.dprint("destroying window %s" % (self))
        self.Destroy()

    def deleteWindowPostHook(self):
        pass

    def createEventBindings(self):
        if hasattr(self.editwin,'addUpdateUIEvent'):
            self.editwin.addUpdateUIEvent(self.OnUpdateUI)
        
        self.editwin.Bind(wx.EVT_KEY_DOWN, self.frame.OnKeyPressed)

        self.idle_update_menu = False
        self.Bind(wx.EVT_IDLE, self.OnIdle)

    def createEventBindingsPostHook(self):
        pass

    def createListeners(self):
        """Required wx.lib.pubsub listeners are created here.

        Subclasses should override createListenersPostHook to add
        their own listeners.
        """
        Publisher().subscribe(self.resetStatusBar, 'resetStatusBar')
        Publisher().subscribe(self.settingsChanged, 'settingsChanged')

    def createListenersPostHook(self):
        """Hook to add custom listeners.

        Subclasses should override this method rather than
        createListeners to add their own listeners.
        """
        pass

    def removeListeners(self):
        """Required wx.lib.pubsub listeners are removed here.

        Subclasses should override removeListenersPostHook to remove
        any listeners that were added.  Normally, wx.lib.pubsub
        removes references to dead objects, and so this cleanup
        shouldn't be necessary.  But, because the MajorMode is
        subclassed from the C++ object, the python part is removed but
        the C++ part isn't cleaned up immediately.  So, we have to
        remove the listener references manually.
        """
        Publisher().unsubscribe(self.resetStatusBar)
        Publisher().unsubscribe(self.settingsChanged)

    def removeListenersPostHook(self):
        """Hook to remove custom listeners.

        Any listeners added by subclasses in createListenersPostHook
        should be removed here.
        """
        pass

    def addPane(self, win, paneinfo):
        self._mgr.AddPane(win, paneinfo)

    def loadMinorModes(self):
        minors=self.settings.minor_modes
        assert self.dprint(minors)
        if minors is not None:
            minorlist=minors.split(',')
            assert self.dprint("loading %s" % minorlist)
            MinorModeLoader(ComponentManager()).load(self,minorlist)
            self.createMinorModeList()

    def createMinorMode(self,minorcls):
        try:
            minor=minorcls(self,self.splitter)
            # register minor mode here
        except MinorModeIncompatibilityError:
            pass

    def createMinorModeList(self):
        minors = self._mgr.GetAllPanes()
        for minor in minors:
            assert self.dprint("name=%s caption=%s window=%s state=%s" % (minor.name, minor.caption, minor.window, minor.state))
            if minor.name != "main":
                self.minors.append(minor)
        self.minors.sort(key=lambda s:s.caption)

    def OnUpdateUI(self, evt):
        """Callback to update user interface elements.

        This event is called when the user interacts with the editing
        window, possibly creating a state change that would require
        some user interface elements to change state.

        Don't depend on this coming from the STC, as non-STC based
        modes won't have the STC_EVT_UPDATEUI event and may be calling
        this using other events.

        @param evt: some event of undetermined type
        """
        assert self.dprint("OnUpdateUI for view %s, frame %s" % (self.keyword,self.frame))
        linenum = self.editwin.GetCurrentLine()
        pos = self.editwin.GetCurrentPos()
        col = self.editwin.GetColumn(pos)
        self.frame.SetStatusText("L%d C%d F%d" % (linenum+self.settings.line_number_offset, col+self.settings.column_number_offset, self.editwin.GetFoldLevel(linenum)&wx.stc.STC_FOLDLEVELNUMBERMASK - wx.stc.STC_FOLDLEVELBASE),1)
        self.idle_update_menu = True
        self.OnUpdateUIHook(evt)
        if evt is not None:
            evt.Skip()

    def OnUpdateUIHook(self, evt):
        pass

    def OnIdle(self, evt):
        if self.idle_update_menu:
            # FIXME: calling the toolbar enable here is better than in
            # the update UI loop, but it still seems to cause flicker
            # in the paste icon.  What's up with that?
            self.frame.enableTools()
            self.idle_update_menu = False
        self.idlePostHook()
        evt.Skip()

    def idlePostHook(self):
        """Hook for subclasses to process during idle time.
        """
        pass

    def createEditWindow(self,parent):
        win=wx.Window(parent, -1)
        win.SetBackgroundColour(wx.ColorRGB(0xabcdef))
        text=self.buffer.stc.GetText()
        wx.StaticText(win, -1, text, (10,10))
        return win

    def createStatusIcons(self):
        """Create any icons in the status bar.

        This is called after making the major mode the active mode in
        the frame.  The status bar will be cleared to its initial
        empty state, so all this method has to do is add any icons
        that it needs.
        """
        pass

    def getStatusBar(self):
        """Returns pointer to this mode's status bar.

        Individual status bars are maintained by each instance of a
        major mode.  The frame only shows the status bar of the active
        mode and hides all the rest.  This means that modes may change
        their status bars without checking if they are the active
        mode.  This situation arizes when there is some background
        processing going on (either with threads or using wx.Yield)
        and the user switches to some other mode.
        """
        if self.statusbar is None:
            self.statusbar = PeppyStatusBar(self.frame)
            self.createStatusIcons()
        return self.statusbar

    def resetStatusBar(self, message=None):
        """Updates the status bar.

        This method clears and rebuilds the status bar, usually
        because something requests an icon change.
        """
        self.statusbar.reset()
        self.createStatusIcons()

    def setMinibuffer(self,minibuffer=None):
        self.removeMinibuffer()
        if minibuffer is not None:
            self.minibuffer=minibuffer
            box=self.GetSizer()
            box.Add(self.minibuffer.win,0,wx.EXPAND)
            self.Layout()
            self.minibuffer.win.Show()
            self.minibuffer.focus()

    def removeMinibuffer(self, detach_only=False):
        dprint(self.minibuffer)
        if self.minibuffer is not None:
            box=self.GetSizer()
            box.Detach(self.minibuffer.win)
            if not detach_only:
                # for those cases where you still want to keep a
                # pointer around to the minibuffer and close it later,
                # use detach_only
                self.minibuffer.close()
            self.minibuffer=None
            self.Layout()
            self.focus()

    def reparent(self,parent):
        self.Reparent(parent)

    def addPopup(self,popup):
        self.popup=popup
        self.Bind(wx.EVT_RIGHT_DOWN, self.popupMenu)

    def popupMenu(self,evt):
        # popups generate menu events as normal menus, so the
        # callbacks to the command objects should still work if the
        # popup is generated in the same way that the regular menu and
        # toolbars are.
        assert self.dprint("popping up menu for %s" % evt.GetEventObject())
        self.PopupMenu(self.popup)
        evt.Skip()

    def applySettings(self):
        """Apply settings to the view

        This is the place where settings for the class show their
        effects.  Calling this should update the view to reflect any
        changes in the settings.
        """
        pass

    def settingsChanged(self, message=None):
        dprint("changing settings for mode %s" % self.__class__.__name__)
        self.applySettings()       

    def focus(self):
        #assert self.dprint("View: setting focus to %s" % self)
        self.editwin.SetFocus()
        self.focusPostHook()

    def focusPostHook(self):
        pass

    def showModified(self,modified):
        self.frame.showModified(self)

    def showBusy(self, busy):
        self.Enable(not busy)
        if busy:
            cursor = wx.StockCursor(wx.CURSOR_WATCH)
        else:
            cursor = wx.StockCursor(wx.CURSOR_DEFAULT)
        self.editwin.SetCursor(cursor)

    def getFunctionList(self):
        '''
        Return a list of tuples, where each tuple contains information
        about a notable line in the source code corresponding to a
        class, a function, a todo item, etc.
        '''
        return ([], [], {}, [])



class MajorModeMatch(object):
    """
    Return type of a L{IMajorModeMatcher} when a successful match is
    made.  In addition of the View class, any name/value pairs
    specific to this file can be passed back to the caller, as well as
    an indicator if the match is exact or generic.

    """
    
    def __init__(self,view,generic=False,exact=True,editable=True):
        self.view=view
        self.vars={}
        if generic:
            self.exact=False
        else:
            self.exact=True
        self.editable=True


class IMajorModeMatcher(Interface):
    """
    Interface that
    L{MajorModeMatcherDriver<views.MajorModeMatcherDriver>} uses to
    determine if a View represented by this plugin is capable of
    viewing the data in the buffer.  (Note that one IMajorModeMatcher
    may represent more than one View.)  Several methods are used in an
    attempt to pick the best match for the data in the buffer.

    First, if the first non-blank line in the buffer (or second line
    if the first contains the shell string C{#!}) contains the emacs
    mode, the L{scanEmacs} method will be called to see if your plugin
    recognizes the emacs major mode string within the C{-*-} delimiters.

    Secondly, if the first line starts with C{#!}, the rest of the line
    is passed to L{scanShell} method to see if it looks like an shell
    command.

    If neither of these methods return a View, then the user hasn't
    explicitly named the view, so we need to determine which View to
    use based on either the filename or by scanning the contents.

    The first of the subsequent search methods is also the simplest:
    L{scanFilename}.  If a pattern (typically the filename extension)
    is recognized, that view is used.

    Next in order, L{scanMagic} is called to see if some pattern in
    the text can be used to identify the file type.
    """

    def scanEmacs(emacsmode,vars):
        """
        This method is called if the first non-blank line in the
        buffer (or second line if the first contains the shell string
        C{#!}) contains an emacs major mode specifier.  Emacs
        recognizes a string in the form of::

          -*-C++-*-
          -*- mode: Python; -*-
          -*- mode: Ksh; var1:value1; var3:value9; -*-
      
        The text within the delimiters is parsed by the
        L{MajorModeMatcherDriver<views.MajorModeMatcherDriver>}, and
        two parameters are passed to this method.  The emacs mode
        string is passed in as C{emacsmode}, and any name/value pairs
        are passed in the C{vars} dict (which could be empty).  It is
        not required that your plugin understand and process the
        variables.

        If your plugin recognizes the emacs major mode string, return
        a L{MajorModeMatch} object that contains the View class.
        Otherwise, return None and the
        L{MajorModeMatcherDriver<views.MajorModeMatcherDriver>} will
        continue processing.

        @param emacsmode: text string of the Emacs major mode
        @type emacsmode: string
        @param vars: name:value pairs, can be the empty list
        @type vars: list
        """

    def scanShell(bangpath):
        """
        Called if the first line starts with the system shell string
        C{#!}.  The remaining characters from the first line are
        passed in as C{bangpath}.

        If your plugin recognizes something in the shell string,
        return a L{MajorModeMatch} object that contains the View class and
        the L{MajorModeMatcherDriver<views.MajorModeMatcherDriver>}
        will stop looking and use than View.  If not, return None and
        the L{MajorModeMatcherDriver<views.MajorModeMatcherDriver>}
        will continue processing.

        @param bangpath: characters after the C{#!}
        @type bangpath: string
        """

    def scanFilename(filename):
        """
        Called to see if a pattern in the filename can be identified
        that determines the file type and therefore the
        L{View<views.View>} that should be used.

        If your plugin recognizes something, return a L{MajorModeMatch}
        object with the optional indicators filled in.  If not, return
        None and the
        L{MajorModeMatcherDriver<views.MajorModeMatcherDriver>} will
        continue processing.

        @param filename: filename, can be in URL form
        @type filename: string
        """
    
    def scanURLInfo(url):
        """
        Called to see if the url can be matched to the
        L{MajorMode} that should be used.

        If your plugin recognizes something, return a L{MajorModeMatch}
        object with the optional indicators filled in.  If not, return
        None and the
        L{MajorModeMatcherDriver<views.MajorModeMatcherDriver>} will
        continue processing.

        @param url: filename, can be in URL form
        @type url: URLInfo
        """
    
    def scanMagic(buffer):
        """
        Called to see if a pattern in the text can be identified that
        determines the file type and therefore the L{View<views.View>}
        that should be used.

        If your plugin recognizes something, return a L{MajorModeMatch}
        object with the optional indicators filled in.  If not, return
        None and the
        L{MajorModeMatcherDriver<views.MajorModeMatcherDriver>} will
        continue processing.

        @param buffer: buffer of already loaded file
        @type buffer: L{Buffer<buffers.Buffer>}
        """

class MajorModeMatcherBase(Component):
    """
    Simple do-nothing base class for L{IMajorModeMatcher}
    implementations so that you don't have to provide null methods for
    scans you don't want to implement.

    @see: L{IMajorModeMatcher} for the interface description

    @see: L{FundamentalPlugin<fundamental.FundamentalPlugin>} or
    L{PythonPlugin} for examples.
    """
    def possibleModes(self):
        """Return list of possible major modes.

        A subclass that extends MajorModeMatcherBase should return a
        list or generator of all the major modes that this matcher is
        representing.  Generally, a matcher will only represent a
        single mode, but it is possible to represent more.

        @returns: list of MajorMode classes
        """
        return []

    def possibleEmacsMappings(self):
        """Return a list of emacs names to major modes.

        A subclass that extends MajorModeMatcherBase should return a
        list or generator that maps an emacs mode name to the peppy
        MajorMode.  The emacs mode name is the string that emacs uses
        to recognize a major mode; for instance 'hexl' is used to
        represent the hex editig mode in emacs, but it is known as
        'HexEdit' in peppy.  So, the L{HexEditPlugin} defines a
        mapping from 'hexl' to L{HexEditMode}.

        Generally, a matcher will only represent a single mode, but it
        is possible to represent more.

        @returns: tuple of (string, MajorMode class)
        """
        return []
    
    def scanEmacs(self,emacsmode,vars):
        # match mode keyword against emacs mode string
        for mode in self.possibleModes():
            if emacsmode.lower() == mode.keyword.lower():
                return MajorModeMatch(mode,exact=True)
        # try to match the mode's alternate emacs strings
        for keyword, mode in self.possibleEmacsMappings():
            if emacsmode.lower() == keyword.lower():
                return MajorModeMatch(mode,exact=True)
        return None

    def scanShell(self,bangpath):
        text = bangpath.lower()
        for mode in self.possibleModes():
            keyword = mode.keyword.lower()

            # only match words that are bounded by some sort of
            # non-word delimiter.  For instance, if the mode is
            # "test", it will match "/usr/bin/test" or
            # "/usr/bin/test.exe" or "/usr/bin/env test", but not
            # /usr/bin/testing or /usr/bin/attested
            match=re.search(r'[\W]%s([\W]|$)' % keyword, text)
            if match:
                return MajorModeMatch(mode,exact=True)
        return None

    def scanFilename(self,filename):
        for mode in self.possibleModes():
            if mode.regex:
                match=re.search(mode.regex,filename)
                if match:
                    return MajorModeMatch(mode,exact=True)
        return None
    
    def scanURLInfo(self, url):
        return None
    
    def scanMagic(self,buffer):
        return None
    


class MajorModeMatcherDriver(Component,debugmixin):
    debuglevel=0
    plugins=ExtensionPoint(IMajorModeMatcher)
    implements(IMenuItemProvider)

    default_menu=(("View",MenuItem(MajorModeSelect).first()),
                  )

    def getMenuItems(self):
        for menu,item in self.default_menu:
            yield (None,menu,item)


    def parseEmacs(self,line):
        """
        Parse a potential emacs major mode specifier line into the
        mode and the optional variables.  The mode may appears as any
        of::

          -*-C++-*-
          -*- mode: Python; -*-
          -*- mode: Ksh; var1:value1; var3:value9; -*-

        @param line: first or second line in text file
        @type line: string
        @return: two-tuple of the mode and a dict of the name/value pairs.
        @rtype: tuple
        """
        match=re.search(r'-\*\-\s*(mode:\s*(.+?)|(.+?))(\s*;\s*(.+?))?\s*-\*-',line)
        if match:
            vars={}
            varstring=match.group(5)
            if varstring:
                try:
                    for nameval in varstring.split(';'):
                        s=nameval.strip()
                        if s:
                            name,val=s.split(':')
                            vars[name.strip()]=val.strip()
                except:
                    pass
            if match.group(2):
                return (match.group(2),vars)
            elif match.group(3):
                return (match.group(3),vars)
        return None

    def scanURL(self, url):
        """
        Determine the best possible L{MajorMode} subclass for the
        given buffer, but only using the buffer's metadata and without
        reading any of the buffer itself.  See L{IMajorModeMatcher} for more information
        on designing plugins that this method uses.

        The URL is searched first, and if no match is found with any component
        of the URL, the filename is checked for regular expressions.

        If an exact match, the searching stops and that mode is returned.  If
        there is a generic match, the searching continues for a possibly
        better match.

        If only generic matches are left ... figure out some way to
        choose the best one.

        @param url: URLInfo object to scan
        
        @returns: the best view for the buffer
        @rtype: L{MajorMode} subclass
        """

        generics = []
        for plugin in self.plugins:
            best=plugin.scanURLInfo(url)
            if best is not None:
                if best.exact:
                    return best.view
                else:
                    assert self.dprint("scanFilename: appending generic %s" % best.view)
                    generics.append(best)
        for plugin in self.plugins:
            best=plugin.scanFilename(url.path)
            if best is not None:
                if best.exact:
                    return best.view
                else:
                    assert self.dprint("scanFilename: appending generic %s" % best.view)
                    generics.append(best)
        return generics


    def scanBuffer(self,buffer):
        """
        Determine the best possible L{MajorMode} subclass for the
        given buffer.  See L{IMajorModeMatcher} for more information
        on designing plugins that this method uses.

        Emacs-style major mode strings are searched for first, and if
        a match is found, immediately returns that MajorMode.
        Bangpath lines are then searched, also returning immediately
        if identified.

        If neither of those cases match, a more complicated search
        procedure is used.  If a filename match is determined to be an
        exact match, that MajorMode is used.  But, if the filename
        match is only a generic match, searching continues.  Magic
        values within the file are checked, and again if an exact
        match is found the MajorMode is returned.

        If only generic matches are left ... figure out some way to
        choose the best one.

        @param buffer: the buffer of interest
        @type buffer: L{Buffer<buffers.Buffer>}

        @returns: the best view for the buffer
        @rtype: L{MajorMode} subclass
        """
        
        bangpath=buffer.stc.GetLine(0)
        if bangpath.startswith('#!'):
            emacs=self.parseEmacs(bangpath + buffer.stc.GetLine(1))
        else:
            emacs=self.parseEmacs(bangpath)
            bangpath=None
        assert self.dprint("bangpath=%s" % bangpath)
        assert self.dprint("emacs=%s" % str(emacs))
        best=None
        generics=[]
        if emacs is not None:
            for plugin in self.plugins:
                best=plugin.scanEmacs(*emacs)
                if best is not None:
                    if best.exact:
                        return best.view
                    else:
                        assert self.dprint("scanEmacs: appending generic %s" % best.view)
                        generics.append(best)
        if bangpath is not None:
            for plugin in self.plugins:
                best=plugin.scanShell(bangpath)
                if best is not None:
                    if best.exact:
                        return best.view
                    else:
                        assert self.dprint("scanShell: appending generic %s" % best.view)
                        generics.append(best)
        for plugin in self.plugins:
            best=plugin.scanMagic(buffer)
            if best is not None:
                if best.exact:
                    return best.view
                else:
                    assert self.dprint("scanMagic: appending generic %s" % best.view)
                    generics.append(best)
        if generics:
            assert self.dprint("Choosing from generics: %s" % [g.view for g in generics])
            # FIXME: don't just use the first one, do something smarter!
            return generics[0].view
        else:
            # FIXME: need to specify the global default mode, but not
            # like this.  The plugins should manage it.  Maybe add a
            # method to the IMajorModeMatcher to see if this mode is
            # the default mode.
            return FundamentalMode

def ScanBufferForMajorMode(buffer):
    """
    Application-wide entry point used to find the best view for the
    given buffer.

    @param buffer: the newly loaded buffer
    @type buffer: L{Buffer<buffers.Buffer>}
    """
    
    comp_mgr=ComponentManager()
    driver=MajorModeMatcherDriver(comp_mgr)
    mode = driver.scanBuffer(buffer)
    return mode

def InitialGuessMajorMode(url):
    """
    Application-wide entry point used to find the best view for the
    given buffer.

    @param buffer: the newly loaded buffer
    @type buffer: L{Buffer<buffers.Buffer>}
    """
    
    comp_mgr=ComponentManager()
    driver=MajorModeMatcherDriver(comp_mgr)
    mode = driver.scanURL(url)
    return mode
