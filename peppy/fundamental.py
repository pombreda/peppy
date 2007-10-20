# peppy Copyright (c) 2006-2007 Rob McMullen
# Licenced under the GPL; see http://www.flipturn.org/peppy for more info
import os, shutil, time, new

import wx
import wx.stc
from wx.lib.pubsub import Publisher

from peppy.menu import *
from peppy.major import *

from peppy.editra import *
from peppy.editra.stcmixin import *

class BraceHighlightMixin(object):
    """Brace highlighting mixin for STC

    Highlight matching braces or flag mismatched braces.  This is
    called during the EVT_STC_UPDATEUI event handler.

    Code taken from StyledTextCtrl_2 from the wxPython demo.  Should
    probably implement this as a dynamic method of the text control or
    the Major Mode, controllable by a setting.
    """
    def braceHighlight(self):
        # check for matching braces
        braceAtCaret = -1
        braceOpposite = -1
        charBefore = None
        caretPos = self.GetCurrentPos()

        if caretPos > 0:
            charBefore = self.GetCharAt(caretPos - 1)
            styleBefore = self.GetStyleAt(caretPos - 1)

        # check before
        if charBefore and chr(charBefore) in "[]{}()" and styleBefore == wx.stc.STC_P_OPERATOR:
            braceAtCaret = caretPos - 1

        # check after
        if braceAtCaret < 0:
            charAfter = self.GetCharAt(caretPos)
            styleAfter = self.GetStyleAt(caretPos)

            if charAfter and chr(charAfter) in "[]{}()" and styleAfter == wx.stc.STC_P_OPERATOR:
                braceAtCaret = caretPos

        if braceAtCaret >= 0:
            braceOpposite = self.BraceMatch(braceAtCaret)

        if braceAtCaret != -1  and braceOpposite == -1:
            self.BraceBadLight(braceAtCaret)
        else:
            self.BraceHighlight(braceAtCaret, braceOpposite)
        


class StandardReturnMixin(object):
    def findIndent(self, linenum):
        """Find proper indention of next line given a line number.

        This is designed to be overridden in subclasses.  Given the
        current line, figure out what the indention should be for the
        next line.
        """
        return self.GetLineIndentation(linenum)
        
    def electricReturn(self):
        """Add a newline and indent to the proper tab level.

        Indent to the level of the line above.
        """
        linesep = self.getLinesep()

        # reindent current line (if necessary), then process the return
        pos = self.reindentLine()
        
        linenum = self.GetCurrentLine()
        #pos = self.GetCurrentPos()
        col = self.GetColumn(pos)
        linestart = self.PositionFromLine(linenum)
        line = self.GetLine(linenum)[:pos-linestart]
    
        #get info about the current line's indentation
        ind = self.GetLineIndentation(linenum)

        dprint("format = %s col=%d ind = %d" % (repr(linesep), col, ind)) 

        self.SetTargetStart(pos)
        self.SetTargetEnd(pos)
        if col <= ind:
            newline = linesep+self.GetIndentString(col)
        elif not pos:
            newline = linesep
        else:
            ind = self.findIndent(linenum)
            newline = linesep+self.GetIndentString(ind)
        self.ReplaceTarget(newline)
        self.GotoPos(pos + len(newline))


class ReindentBase(object):
    def reindentLine(self, linenum=None):
        """Reindent the specified line to the correct level.

        Given a line, indent to the previous line
        """
        if linenum is None:
            linenum = self.GetCurrentLine()
        if linenum == 0:
            # first line is always indented correctly
            return self.GetCurrentPos()
        
        linestart = self.PositionFromLine(linenum)

        # actual indention of current line
        indcol = self.GetLineIndentation(linenum) # columns
        pos = self.GetCurrentPos()
        indpos = self.GetLineIndentPosition(linenum) # absolute character position
        col = self.GetColumn(pos)
        dprint("linestart=%d pos=%d indpos=%d col=%d indcol=%d" % (linestart, pos, indpos, col, indcol))

        indstr = self.getReindentString(linenum, linestart, pos, indpos, col, indcol)
        if indstr is None:
            return pos
        
        # the target to be replaced is the leading indention of the
        # current line
        self.SetTargetStart(linestart)
        self.SetTargetEnd(indpos)
        self.ReplaceTarget(indstr)

        # recalculate cursor position, because it may have moved if it
        # was within the target
        after = self.GetLineIndentPosition(linenum)
        dprint("after: indent=%d cursor=%d" % (after, self.GetCurrentPos()))
        if pos < linestart:
            return pos
        newpos = pos - indpos + after
        if newpos < linestart:
            # we were in the indent region, but the region was made smaller
            return after
        elif pos < indpos:
            # in the indent region
            return after
        return newpos

    def getReindentString(self, linenum, linestart, pos, indpos, col, indcol):
        return None


class StandardReindentMixin(ReindentBase):
    def getReindentString(self, linenum, linestart, pos, indpos, col, indcol):
        # look at indention of previous line
        prevind, prevline = self.GetPrevLineIndentation(linenum)
        if (prevind < indcol and prevline < linenum-1) or prevline < linenum-2:
            # if there's blank lines before this and the previous
            # non-blank line is indented less than this one, ignore
            # it.  Make the user manually unindent lines.
            return None

        # previous line is not blank, so indent line to previous
        # line's level
        return self.GetIndentString(prevind)


class FoldingReindentMixin(object):
    def reindentLine(self, linenum=None):
        """Reindent the specified line to the correct level.

        Given a line, use Scintilla's built-in folding to determine
        the indention level of the current line.
        """
        if linenum is None:
            linenum = self.GetCurrentLine()
        linestart = self.PositionFromLine(linenum)

        # actual indention of current line
        ind = self.GetLineIndentation(linenum) # columns
        pos = self.GetLineIndentPosition(linenum) # absolute character position

        # folding says this should be the current indention
        fold = self.GetFoldLevel(linenum)&wx.stc.STC_FOLDLEVELNUMBERMASK - wx.stc.STC_FOLDLEVELBASE
        dprint("ind = %s (char num=%d), fold = %s" % (ind, pos, fold))
        self.SetTargetStart(linestart)
        self.SetTargetEnd(pos)
        self.ReplaceTarget(self.GetIndentString(fold))

class StandardCommentMixin(object):
    def setCommentDelimiters(self, start='', end=''):
        """Set instance-specific comment characters
        
        If the instance uses different comment characters that the class
        attributes, set the instance attributes here which will override
        the class attributes.
        
        This is typically called by the Editra stc mixin to set the
        comment characters encoded by the Editra style manager.
        """
        self.start_line_comment = start
        self.end_line_comment = end
        
    def commentRegion(self, add=True):
        """Comment or uncomment a region.

        @param add: True to add comments, False to remove them
        """
        eol_len = len(self.getLinesep())
        if add:
            func = self.addLinePrefixAndSuffix
        else:
            func = self.removeLinePrefixAndSuffix
        
        self.BeginUndoAction()
        line, lineend = self.GetLineRegion()
        assert self.dprint("lines: %d - %d" % (line, lineend))
        try:
            selstart, selend = self.GetSelection()
            assert self.dprint("selection: %d - %d" % (selstart, selend))

            start = selstart
            end = self.GetLineEndPosition(line)
            while line <= lineend:
                start = func(start, end, self.start_line_comment, self.end_line_comment)
                line += 1
                end = self.GetLineEndPosition(line)
            self.SetSelection(selstart, start - eol_len)
        finally:
            self.EndUndoAction()
    
class GenericFoldHierarchyMixin(object):
    def getFoldHierarchy(self):
        """Get the fold hierarchy using Stani's fold explorer algorithm.
        """
        # Turn this into a threaded operation if it takes too long
        t = time.time()
        self.Colourise(0, self.GetTextLength())
        dprint("Finished colourise: %0.5f" % (time.time() - t))
        self.computeFoldHierarchy()
        
        # FIXME: Note that different views of the same buffer *using the
        # same major mode* will have the same fold hierarchy.  This should
        # be exploited, but currently it isn't.
        return self.fold_explorer_root


class FundamentalSTC(BraceHighlightMixin, StandardReturnMixin,
                    StandardReindentMixin, StandardCommentMixin,
                    GenericFoldHierarchyMixin, EditraSTCMixin, PeppySTC):
    
    # Default comment characters in case the Editra styling database
    # doesn't have any information about the mode
    start_line_comment = ''
    end_line_comment = ''

    def __init__(self, parent, refstc=None, **kwargs):
        PeppySTC.__init__(self, parent, refstc, **kwargs)
        EditraSTCMixin.__init__(self, FundamentalMode.getStyleFile())


class FundamentalMode(MajorMode):
    """
    The base view of most (if not all) of the views that use the STC
    to directly edit the text.  Views (like the HexEdit view or an
    image viewer) that only use the STC as the backend storage are
    probably not based on this view.
    """
    keyword = 'Fundamental'
    
    # If the editra file_type (defined as the LANG_* keywords in the
    # editra source file peppy/editra/synglob.py) doesn't match the keyword
    # above, specify the editra file type here.  In other words, None here
    # means that the editra file_type *does* match the keyword
    editra_synonym = None

    # STC class used as the viewer (note: differs from stc_class, which is
    # the STC class used as the storage backend)
    stc_viewer_class = FundamentalSTC

    default_classprefs = (
        StrParam('editra_style_sheet', 'styles.ess', 'Filename in the config directory containing\nEditra style sheet information'),
        BoolParam('use_tab_characters', False,
                  'True: insert tab characters when tab is pressed\nFalse: insert the equivalent number of spaces instead.'),
        IntParam('tab_size', 4, 'Number of spaces in each tab'),
        IndexChoiceParam('tab_highlight_style',
                         ['ignore', 'inconsistent', 'mixed', 'spaces are bad', 'tabs are bad'],
                         4, 'Highlight bad intentation'),
        BoolParam('line_numbers', True, 'Show line numbers in the margin?'),
        IntParam('line_number_margin_width', 40, 'Margin width in pixels'),
        BoolParam('symbols', False, 'Show symbols margin'),
        IntParam('symbols_margin_width', 16, 'Symbols margin width in pixels'),
        BoolParam('folding', False, 'Show the code folding margin?'),
        IntParam('folding_margin_width', 16, 'Code folding margin width in pixels'),
        BoolParam('word_wrap', False, 'True: use word wrapping\nFalse: show horizontal scrollbars'),
        BoolParam('backspace_unindents', True),
        BoolParam('indentation_guides', True, 'Show indentation guides at multiples of the tab_size'),
        IntParam('highlight_column', 30, 'Column at which to highlight the indention guide.\nNote: uses the BRACELIGHT color to highlight'),
        IntParam('edge_column', 80, 'Column at which to show the edge (i.e. long line) indicator'),
        KeyedIndexChoiceParam('edge_indicator',
                              [(wx.stc.STC_EDGE_NONE, 'none'),
                               (wx.stc.STC_EDGE_LINE, 'line'),
                               (wx.stc.STC_EDGE_BACKGROUND, 'background'),
                               ], 'line', help='Long line indication mode'),
        IntParam('caret_blink_rate', 0, help='Blink rate in milliseconds\nor 0 to stop blinking'),
        IntParam('caret_width', 2, help='Caret width in pixels'),
        BoolParam('caret_line_highlight', False, help='Highlight the line containing the cursor?'),
        )
    
    @classmethod
    def verifyEditraType(cls, ext, file_type):
        cls.dprint("ext=%s file_type=%s" % (ext, file_type))
        if file_type is None:
            # Not recognized at all by Editra.
            return False
        
        # file_type is a human readable string given in peppy.editra.synglob.py
        # If file_type is the same as the major mode keyword or an alias,
        # mark this as a specific match.
        if file_type == cls.keyword or file_type == cls.editra_synonym:
            cls.dprint("Specific match of %s" % file_type)
            return ext
        
        # Otherwise, if the file type is recognized but not specific to this
        # mode, mark it as generic.
        cls.dprint("generic match of %s" % file_type)
        return "generic"
    
    @classmethod
    def getStyleFile(cls):
        filename = wx.GetApp().getConfigFilePath(cls.classprefs.editra_style_sheet)
        dprint(filename)
        return filename
    
    def createEditWindow(self,parent):
        assert self.dprint("creating new Fundamental window")
        self.createSTC(parent)
        win=self.stc
        return win

    def createStatusIcons(self):
        linesep = self.stc.getLinesep()
        if linesep == '\r\n':
            self.statusbar.addIcon("icons/windows.png", "DOS/Windows line endings")
        elif linesep == '\r':
            self.statusbar.addIcon("icons/apple.png", "Old-style Apple line endings")
        else:
            self.statusbar.addIcon("icons/tux.png", "Unix line endings")

    def createSTC(self,parent):
        """Create the STC and apply styling settings.

        Everything that subclasses from FundamentalMode will use an
        STC instance for displaying the user interaction window.
        
        Styling information is loaded from the stc-styles.rc.cfg files
        that the boa styling editor uses.  This file is located in the
        default configuration directory of the application on a
        per-user basis, and in the peppy/config directory on a
        site-wide basis.
        """
        start = time.time()
        self.dprint("starting createSTC at %0.5fs" % start)
        self.stc=self.stc_viewer_class(parent,refstc=self.buffer.stc)
        self.dprint("PeppySTC done in %0.5fs" % (time.time() - start))
        self.applySettings()
        self.dprint("applySettings done in %0.5fs" % (time.time() - start))
        
    def applySettings(self):
        start = time.time()
        self.dprint("starting applySettings at %0.5fs" % start)
        self.applyDefaultSettings()
        #dprint("applyDefaultSettings done in %0.5fs" % (time.time() - start))
        
        ext, file_type = MajorModeMatcherDriver.getEditraType(self.buffer.url.path)
        self.dprint("ext=%s file_type=%s" % (ext, file_type))
        if file_type == 'generic' or file_type is None:
            if self.editra_synonym is not None:
                file_type = self.editra_synonym
            elif self.keyword is not 'Fundamental':
                file_type = self.keyword
            else:
                file_type = ext
        self.editra_lang = file_type
        self.dprint("ext=%s file_type=%s" % (ext, file_type))
        self.stc.SetStyleFont(wx.GetApp().classprefs.primary_editing_font)
        self.stc.SetStyleFont(wx.GetApp().classprefs.secondary_editing_font, False)
        self.stc.ConfigureLexer(self.editra_lang)
        self.dprint("styleSTC (if True) done in %0.5fs" % (time.time() - start))
        self.has_stc_styling = True
        self.dprint("applySettings returning in %0.5fs" % (time.time() - start))
    
    def applyDefaultSettings(self):
        # turn off symbol margin
        if self.classprefs.symbols:
            self.stc.SetMarginWidth(1, self.classprefs.symbols_margin_width)
        else:
            self.stc.SetMarginWidth(1, 0)

        # turn off folding margin
        if self.classprefs.folding:
            self.stc.SetMarginWidth(2, self.classprefs.folding_margin_width)
        else:
            self.stc.SetMarginWidth(2, 0)

        self.stc.SetProperty("fold", "1")
        self.stc.SetBackSpaceUnIndents(self.classprefs.backspace_unindents)
        self.stc.SetIndentationGuides(self.classprefs.indentation_guides)
        self.stc.SetHighlightGuide(self.classprefs.highlight_column)

        self.setWordWrap()
        self.setLineNumbers()
        self.setFolding()
        self.setTabStyle()
        self.setEdgeStyle()
        self.setCaretStyle()

    def setWordWrap(self,enable=None):
        if enable is not None:
            self.classprefs.word_wrap=enable
        if self.classprefs.word_wrap:
            self.stc.SetWrapMode(wx.stc.STC_WRAP_CHAR)
            self.stc.SetWrapVisualFlags(wx.stc.STC_WRAPVISUALFLAG_END)
        else:
            self.stc.SetWrapMode(wx.stc.STC_WRAP_NONE)

    def setLineNumbers(self,enable=None):
        if enable is not None:
            self.classprefs.line_numbers=enable
        if self.classprefs.line_numbers:
            self.stc.SetMarginType(0, wx.stc.STC_MARGIN_NUMBER)
            self.stc.SetMarginWidth(0,  self.classprefs.line_number_margin_width)
        else:
            self.stc.SetMarginWidth(0,0)

    def setFolding(self,enable=None):
        if enable is not None:
            self.classprefs.folding=enable
        if self.classprefs.folding:
            self.stc.SetMarginType(2, wx.stc.STC_MARGIN_SYMBOL)
            self.stc.SetMarginMask(2, wx.stc.STC_MASK_FOLDERS)
            self.stc.SetMarginSensitive(2, True)
            self.stc.SetMarginWidth(2, self.classprefs.folding_margin_width)
            # Marker definitions from PyPE
            self.stc.MarkerDefine(wx.stc.STC_MARKNUM_FOLDEREND,     wx.stc.STC_MARK_BOXPLUSCONNECTED,  "white", "black")
            self.stc.MarkerDefine(wx.stc.STC_MARKNUM_FOLDEROPENMID, wx.stc.STC_MARK_BOXMINUSCONNECTED, "white", "black")
            self.stc.MarkerDefine(wx.stc.STC_MARKNUM_FOLDERMIDTAIL, wx.stc.STC_MARK_TCORNER,  "white", "black")
            self.stc.MarkerDefine(wx.stc.STC_MARKNUM_FOLDERTAIL,    wx.stc.STC_MARK_LCORNER,  "white", "black")
            self.stc.MarkerDefine(wx.stc.STC_MARKNUM_FOLDERSUB,     wx.stc.STC_MARK_VLINE,    "white", "black")
            self.stc.MarkerDefine(wx.stc.STC_MARKNUM_FOLDER,        wx.stc.STC_MARK_BOXPLUS,  "white", "black")
            self.stc.MarkerDefine(wx.stc.STC_MARKNUM_FOLDEROPEN,    wx.stc.STC_MARK_BOXMINUS, "white", "black")
            self.stc.Bind(wx.stc.EVT_STC_MARGINCLICK, self.onMarginClick)
        else:
            self.stc.SetMarginWidth(2, 0)
            self.stc.Unbind(wx.stc.EVT_STC_MARGINCLICK)

    def setTabStyle(self):
        self.stc.SetIndent(self.classprefs.tab_size)
        self.stc.SetProperty('tab.timmy.whinge.level', str(self.classprefs.tab_highlight_style))
        self.stc.SetUseTabs(self.classprefs.use_tab_characters)

    def setEdgeStyle(self):
        self.stc.SetEdgeMode(self.classprefs.edge_indicator)
        if self.classprefs.edge_indicator == wx.stc.STC_EDGE_NONE:
            self.stc.SetEdgeColumn(0)
        else:
            self.stc.SetEdgeColumn(self.classprefs.edge_column)

    def setCaretStyle(self):
        self.stc.SetCaretPeriod(self.classprefs.caret_blink_rate)
        self.stc.SetCaretLineVisible(self.classprefs.caret_line_highlight)
        self.stc.SetCaretWidth(self.classprefs.caret_width)

    def onMarginClick(self, evt):
        # fold and unfold as needed
        if evt.GetMargin() == 2:
            if evt.GetShift() and evt.GetControl():
                self.stc.FoldAll()
            else:
                lineClicked = self.stc.LineFromPosition(evt.GetPosition())
                if self.stc.GetFoldLevel(lineClicked) & wx.stc.STC_FOLDLEVELHEADERFLAG:
                    if evt.GetShift():
                        self.stc.SetFoldExpanded(lineClicked, True)
                        self.stc.Expand(lineClicked, True, True, 1)
                    elif evt.GetControl():
                        if self.stc.GetFoldExpanded(lineClicked):
                            self.stc.SetFoldExpanded(lineClicked, False)
                            self.stc.Expand(lineClicked, False, True, 0)
                        else:
                            self.stc.SetFoldExpanded(lineClicked, True)
                            self.stc.Expand(lineClicked, True, True, 100)
                    else:
                        self.stc.ToggleFold(lineClicked)

    def OnUpdateUIHook(self, evt):
        self.stc.braceHighlight()
