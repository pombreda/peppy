# peppy Copyright (c) 2006-2007 Rob McMullen
# Licenced under the GPL; see http://www.flipturn.org/peppy for more info
import os,re

import wx
import wx.stc
from wx.lib.pubsub import Publisher

from cStringIO import StringIO

from debug import *


def GetClipboardText():
    success = False
    do = wx.TextDataObject()
    if wx.TheClipboard.Open():
        success = wx.TheClipboard.GetData(do)
        wx.TheClipboard.Close()

    if success:
        return do.GetText()
    return None

def SetClipboardText(txt):
    do = wx.TextDataObject()
    do.SetText(txt)
    if wx.TheClipboard.Open():
        wx.TheClipboard.SetData(do)
        wx.TheClipboard.Close()
        return 1
    return 0


#### STC Interface

class STCInterface(object):
    """
    Methods that a data source object must implement in order to be
    compatible with the real STC used as the data source for
    text-based files.

    See U{the Yellowbrain guide to the
    STC<http://www.yellowbrain.com/stc/index.html>} for more info on
    the rest of the STC methods.
    """
    def CanEdit(self):
        """PyPE compat to show read-only status"""
        return True
    
    def Clear(self):
        pass

    def CanCopy(self):
        return False

    def Copy(self):
        pass

    def CanCut(self):
        return False

    def Cut(self):
        pass

    def CanPaste(self):
        return False

    def Paste(self):
        pass

    def EmptyUndoBuffer(self):
        pass

    def CanUndo(self):
        return False

    def Undo(self):
        pass

    def CanRedo(self):
        return False

    def Redo(self):
        pass

    def GetModify(self):
        return False

    def CreateDocument(self):
        return "notarealdoc"

    def SetDocPointer(self,ptr):
        pass

    def ReleaseDocument(self,ptr):
        pass

    def AddRefDocument(self,ptr):
        pass

    def GetBinaryData(self,start,end):
        return []

    def GuessBinary(self,amount,percentage):
        return False

    def GetFoldLevel(self, line):
        """Return fold level of specified line.

        Return fold level of line, which seems to be the number of
        spaces used to indent the line, plus an offset.
        """
        return wx.stc.STC_FOLDLEVELBASE

    def openPostHook(self, fh):
        """Hook called after the initial open of the file.
        
        Hook here for subclasses of STC to do whatever they need with
        the FilterWrapper object used to load the file.  In some cases it
        might be useful to keep a reference to the filter.

        @param fh: file-like object used to load the file
        """
        fh.close()

    def readFrom(self, fh):
        """Read from filehandle, converting as necessary"""
        pass

    def writeTo(self, fh):
        """Write to filehandle, converting as necessary"""
        pass

    def showStyle(self, linenum=None):
        """Debugging routine to show the styling information on a line.

        Print styling information to stdout to aid in debugging.
        """
        pass

class STCProxy(object):
    """Proxy object to defer requests to a real STC.

    Used to wrap a real STC but supply some custom methods.  This is
    used in the case where the major mode is using a real stc for its
    data storage, but not using the stc for display.  Because the
    major mode depends on an stc interface to manage the user
    interface (enabling/disabling buttons, menu items, etc.), a mode
    that doesn't use the stc for display still has to present an stc
    interface for this purpose.  So, wrapping the buffer's stc in this
    object and reassigning methods as appropriate for the display is
    the way to go.
    """
    def __init__(self, stc):
        self.stc = stc

    def __getattr__(self, name):
        # can't use self.stc.__dict__ because the stc is a swig object
        # and apparently swig attributes don't show up in __dict__.
        # So, this is probably slow.
        if hasattr(self.stc, name):
            return getattr(self.stc, name)
        raise AttributeError


class PeppyBaseSTC(wx.stc.StyledTextCtrl, STCInterface, debugmixin):
    """All the non-GUI enhancements to the STC are here.
    """
    eol2int = {'\r': wx.stc.STC_EOL_CR,
               '\r\n': wx.stc.STC_EOL_CRLF,
               '\n': wx.stc.STC_EOL_LF,
               }
    int2eol = {wx.stc.STC_EOL_CR: '\r',
               wx.stc.STC_EOL_CRLF: '\r\n',
               wx.stc.STC_EOL_LF: '\n',
               }
    
    def __init__(self, parent, refstc=None, copy=None):
        wx.stc.StyledTextCtrl.__init__(self, parent, -1)
        self.ClearAll()
        
        if refstc is not None:
            self.refstc=refstc
            self.docptr=self.refstc.docptr
            self.AddRefDocument(self.docptr)
            self.SetDocPointer(self.docptr)
            self.refstc.addSubordinate(self)
            assert self.dprint("referencing document %s" % self.docptr)
        else:
            self.refstc=None
            self.docptr=self.CreateDocument()
            self.SetDocPointer(self.docptr)
            assert self.dprint("creating new document %s" % self.docptr)
            self.subordinates=[]
            if copy is not None:
                txt = copy.GetStyledText(0,copy.GetTextLength())
                dprint("copying %s from old stc." % repr(txt))
                self.AddStyledText(txt)
        self.maybe_undo_eolmode = None

    def addSubordinate(self,otherstc):
        self.subordinates.append(otherstc)

    def removeSubordinate(self,otherstc):
        self.subordinates.remove(otherstc)

    def readFrom(self, fh, size=None):
        if size is not None:
            txt = fh.read(size)
        else:
            txt = fh.read()
        assert self.dprint("BinaryFilter: reading %d bytes from %s" % (len(txt), fh))

        # Now, need to convert it to two bytes per character
        if len(txt) > 0:
            styledtxt = '\0'.join(txt)+'\0'
            assert self.dprint("styledtxt: length=%d" % len(styledtxt))
            
            self.AddStyledText(styledtxt)
    
    def writeTo(self, fh):
        numchars = self.GetTextLength()
        # Have to use GetStyledText because GetText will truncate the
        # string at the first zero character.
        txt = self.GetStyledText(0, numchars)[0:numchars*2:2]
        assert self.dprint("numchars=%d: writing %d bytes to %s" % (numchars, len(txt), fh))
        assert self.dprint(repr(txt))
        try:
            fh.write(txt)
        except:
            print "BinaryFilter: something went wrong writing to %s" % fh
            raise

    ## Additional functionality
    def checkUndoEOL(self):
        # Check to see if the eol mode has changed.
        if self.maybe_undo_eolmode is not None:
            if self.maybe_undo_eolmode['likely']:
                self.detectLineEndings()
                Publisher().sendMessage('resetStatusBar')
            self.maybe_undo_eolmode = None
        
    def Undo(self):
        wx.stc.StyledTextCtrl.Undo(self)
        self.checkUndoEOL()
        
    def Redo(self):
        wx.stc.StyledTextCtrl.Redo(self)
        self.checkUndoEOL()
        
    def CanEdit(self):
        """PyPE compat"""
        return True

    ## STCInterface additions
    def CanCopy(self):
        return True

    def CanCut(self):
        return True

    def GetBinaryData(self,start,end):
        """
        Convenience function to get binary data out of the STC.  The
        only way to get binary data out of the STC is to use the
        GetStyledText method and chop out every other byte.  Using the
        regular GetText method will stop at the first nul character.

        @param start: first text position
        @param end: last text position
        
        @returns: binary data between start and end-1, inclusive (just
        like standard python array slicing)
        """
        return self.GetStyledText(start,end)[::2]

    def GuessBinary(self,amount,percentage):
        """
        Guess if the text in this file is binary or text by scanning
        through the first C{amount} characters in the file and
        checking if some C{percentage} is out of the printable ascii
        range.

        Obviously this is a poor check for unicode files, so this is
        just a bit of a hack.

        @param amount: number of characters to check at the beginning
        of the file

        @type amount: int
        
        @param percentage: percentage of characters that must be in
        the printable ASCII range

        @type percentage: number

        @rtype: boolean
        """
        endpos=self.GetLength()
        if endpos>amount: endpos=amount
        bin=self.GetBinaryData(0,endpos)
        data = [ord(i) for i in bin]
        binary=0
        for ch in data:
            if (ch<8) or (ch>13 and ch<32) or (ch>126):
                binary+=1
        if binary>(endpos/percentage):
            return True
        return False
        
    def GetLineRegion(self):
        """Get current region, extending to current line if no region
        selected.

        If there's a region selected, extend it if necessary to
        encompass full lines.  If no region is selected, create one
        from the current line.
        """
        start, end = self.GetSelection()
        if start == end:
            linestart = lineend = self.GetCurrentLine()
        else:
            linestart = self.LineFromPosition(start)
            lineend = self.LineFromPosition(end - 1)
        
        start -= self.GetColumn(start)
        end = self.GetLineEndPosition(lineend)
        self.SetSelection(start, end)
        return (linestart, lineend)

    def PasteAtColumn(self, paste=None):
        assert self.dprint("rectangle=%s" % self.SelectionIsRectangle())
        start, end = self.GetSelection()
        assert self.dprint("selection = %d,%d" % (start, end))

        line = self.LineFromPosition(start)
        col = self.GetColumn(start)
        assert self.dprint("line = %d, col=%d" % (line, col))

        if paste is None:
            paste = GetClipboardText()
        self.BeginUndoAction()
        try:
            for insert in paste.splitlines():
                if line >= self.GetLineCount():
                    self.InsertText(self.GetTextLength(), self.getLinesep())
                start = pos = self.PositionFromLine(line)
                last = self.GetLineEndPosition(line)
                
                # FIXME: doesn't work with tabs
                if (pos + col) > last:
                    # need to insert spaces before the rectangular area
                    num = pos + col - last
                    insert = ' '*num + insert
                    pos = last
                else:
                    pos += col
                assert self.dprint("before: (%d,%d) = '%s'" % (start,last,self.GetTextRange(start,last)))
                assert self.dprint("inserting: '%s' at %d" % (insert, pos))
                self.InsertText(pos, insert)
                assert self.dprint("after: (%d,%d) = '%s'" % (start,last+len(insert),self.GetTextRange(start,last+len(insert))))
                line += 1
        finally:
            self.EndUndoAction()

    def detectLineEndings(self, num=1024):
        from pype.parsers import detectLineEndings
        if num > self.GetTextLength():
            num = self.GetTextLength()
        linesep = detectLineEndings(self.GetTextRange(0,num))
        mode = self.eol2int[linesep]
        self.SetEOLMode(mode)

    def ConvertEOLs(self, mode):
        wx.stc.StyledTextCtrl.ConvertEOLs(self, mode)
        self.SetEOLMode(mode)

    def getLinesep(self):
        """Get the current line separator character.

        """
        mode = self.GetEOLMode()
        return self.int2eol[mode]

    def openPostHook(self, fh):
        """Hook called after the initial open of the file.
        
        Hook here for subclasses of STC to do whatever they need with
        the FilterWrapper object used to load the file.  In some cases it
        might be useful to keep a reference to the filter.

        @param filter: filter used to load the file

        @type filter: iofilter.FilterWrapper
        """
        fh.close()
        self.detectLineEndings()

    def showStyle(self, linenum=None):
        if linenum is None:
            linenum = self.GetCurrentLine()

        linestart = self.PositionFromLine(linenum)

        # actual indention of current line
        ind = self.GetLineIndentation(linenum) # columns
        pos = self.GetLineIndentPosition(linenum) # absolute character position
        
        # folding says this should be the current indention
        fold = self.GetFoldLevel(linenum)&wx.stc.STC_FOLDLEVELNUMBERMASK - wx.stc.STC_FOLDLEVELBASE
        
        # get line without indention
        line = self.GetLine(linenum)
        for i in range(len(line)):
            print "pos=%d char=%s style=%d" % (linestart+i, repr(line[i]), self.GetStyleAt(linestart+i) )


class PeppySTC(PeppyBaseSTC):
    """
    Base version of the STC that most major modes will use as the STC
    implementation.
    """
    debuglevel=0
    
    def __init__(self, parent, refstc=None, copy=None):
        PeppyBaseSTC.__init__(self, parent, refstc=refstc, copy=copy)

        self.Bind(wx.stc.EVT_STC_DO_DROP, self.OnDoDrop)
        self.Bind(wx.stc.EVT_STC_DRAG_OVER, self.OnDragOver)
        self.Bind(wx.stc.EVT_STC_START_DRAG, self.OnStartDrag)
        self.Bind(wx.stc.EVT_STC_MODIFIED, self.OnModified)

        self.Bind(wx.EVT_WINDOW_DESTROY, self.OnDestroy)

        self.debug_dnd=False

    def sendEvents(self,evt):
        """
        Send an event to all subordinate STCs
        """
        for otherstc in self.subordinates:
            assert self.dprint("sending event %s to %s" % (evt,otherstc))
            wx.PostEvent(otherstc,evt())

    def addUpdateUIEvent(self, callback):
        """Add the equivalent to STC_UPDATEUI event for UI changes.

        The STC supplies the EVT_STC_UPDATEUI event that fires for
        every change that could be used to update the user interface:
        a text change, a style change, or a selection change.  If the
        editing (viewing) window does not use the STC to display
        information, you should supply the equivalent event for the
        edit window.
        
        @param callback: event handler to execute on event
        """
        self.Bind(wx.stc.EVT_STC_UPDATEUI, callback)
        
    def OnDestroy(self, evt):
        """
        Event handler for EVT_WINDOW_DESTROY. Preserve the clipboard
        contents can be preserved after the window is destroyed so
        that other apps can still grab it.

        @param evt: event
        """
        wx.TheClipboard.Flush()
        evt.Skip()


    def OnStartDrag(self, evt):
        assert self.dprint("OnStartDrag: %d, %s\n"
                       % (evt.GetDragAllowMove(), evt.GetDragText()))

        if self.debug_dnd and evt.GetPosition() < 250:
            evt.SetDragAllowMove(False)     # you can prevent moving of text (only copy)
            evt.SetDragText("DRAGGED TEXT") # you can change what is dragged
            #evt.SetDragText("")             # or prevent the drag with empty text


    def OnDragOver(self, evt):
        assert self.dprint(
            "OnDragOver: x,y=(%d, %d)  pos: %d  DragResult: %d\n"
            % (evt.GetX(), evt.GetY(), evt.GetPosition(), evt.GetDragResult())
            )

        if self.debug_dnd and evt.GetPosition() < 250:
            evt.SetDragResult(wx.DragNone)   # prevent dropping at the beginning of the buffer


    def OnDoDrop(self, evt):
        assert self.dprint("OnDoDrop: x,y=(%d, %d)  pos: %d  DragResult: %d\n"
                       "\ttext: %s\n"
                       % (evt.GetX(), evt.GetY(), evt.GetPosition(), evt.GetDragResult(),
                          evt.GetDragText()))

        if self.debug_dnd and evt.GetPosition() < 500:
            evt.SetDragText("DROPPED TEXT")  # Can change text if needed
            #evt.SetDragResult(wx.DragNone)  # Can also change the drag operation, but it
                                             # is probably better to do it in OnDragOver so
                                             # there is visual feedback

            #evt.SetPosition(25)             # Can also change position, but I'm not sure why
                                             # you would want to...




    def OnModified(self, evt):
##        assert self.dprint("""OnModified
##        Mod type:     %s
##        At position:  %d
##        Lines added:  %d
##        Text Length:  %d
##        Text:         %s\n""" % ( self.transModType(evt.GetModificationType()),
##                                  evt.GetPosition(),
##                                  evt.GetLinesAdded(),
##                                  evt.GetLength(),
##                                  repr(evt.GetText()) ))
        assert self.dprint("(%s) at %d: text=%s" % (self.transModType(evt.GetModificationType()),evt.GetPosition(), repr(evt.GetText())))

        # Since the stc doesn't store the EOL state as an undoable
        # parameter, we have to check for it.
        mod = evt.GetModificationType()
        if mod & (wx.stc.STC_PERFORMED_UNDO | wx.stc.STC_PERFORMED_REDO) and mod & (wx.stc.STC_MOD_INSERTTEXT | wx.stc.STC_MOD_DELETETEXT):
            text = evt.GetText()
            if self.maybe_undo_eolmode is None:
                self.maybe_undo_eolmode = {'total': 0, 'linesep': 0, 'likely': False}
            stats = self.maybe_undo_eolmode
            stats['total'] += 1
            if text == '\n' or text == '\r':
                self.dprint("found eol char")
                stats['linesep'] += 1
            if mod & wx.stc.STC_LASTSTEPINUNDOREDO:
                self.dprint("eol summary: %s" % stats)
                if stats['linesep'] == stats['total'] and stats['linesep'] >= self.GetLineCount()-1:
                    self.dprint("likely that this is a eol change")
                    stats['likely'] = True
        
        evt.Skip()

    def OnUpdateUI(self, evt):
        dprint("(%s) at %d: text=%s" % (self.transModType(evt.GetModificationType()),evt.GetPosition(), repr(evt.GetText())))
        evt.Skip()


    def transModType(self, modType):
        st = ""
        table = [(wx.stc.STC_MOD_INSERTTEXT, "InsertText"),
                 (wx.stc.STC_MOD_DELETETEXT, "DeleteText"),
                 (wx.stc.STC_MOD_CHANGESTYLE, "ChangeStyle"),
                 (wx.stc.STC_MOD_CHANGEFOLD, "ChangeFold"),
                 (wx.stc.STC_PERFORMED_USER, "UserFlag"),
                 (wx.stc.STC_PERFORMED_UNDO, "Undo"),
                 (wx.stc.STC_PERFORMED_REDO, "Redo"),
                 (wx.stc.STC_LASTSTEPINUNDOREDO, "Last-Undo/Redo"),
                 (wx.stc.STC_MOD_CHANGEMARKER, "ChangeMarker"),
                 (wx.stc.STC_MOD_BEFOREINSERT, "B4-Insert"),
                 (wx.stc.STC_MOD_BEFOREDELETE, "B4-Delete")
                 ]

        for flag,text in table:
            if flag & modType:
                st = st + text + " "

        if not st:
            st = 'UNKNOWN'

        return st



class NonResidentSTC(STCInterface,debugmixin):
    """Non-memory-resident version of the STC.
    
    Base version of a non-memory resident storage space that
    implements the STC interface.
    """
    debuglevel=0
    
    def __init__(self, parent=None, copy=None):
        self.filename = None

    def CanEdit(self):
        return False
        
    def openPostHook(self, fh):
        if fh.urlinfo.protocol == 'file':
            self.filename = fh.urlinfo.path
        else:
            raise TypeError("url must be a file. %s" % fh.urlinfo.url)

        fh.close()

        self.openMmap()

    def openMmap(self):
        pass
    

class MmapSTC(NonResidentSTC):
    def openMmap(self):
        self.fh = open(self.filename)
        self.mmap = mmap.mmap(self.fh.fileno(), 0, access=mmap.ACCESS_READ)
        dprint(self.mmap)

    def GetTextLength(self):
        return self.mmap.size()

    def GetBinaryData(self, start, end):
        self.mmap.seek(start)
        return self.mmap.read(end-start)


