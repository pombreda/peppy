import os,re

import wx
import wx.stc as stc

from menudev import FrameAction
from stcinterface import *
from configprefs import *

from debug import *



class ViewAction(FrameAction):
    # Set up a new run() method to pass the viewer
    def run(self, state=None, pos=-1):
        self.dprint("id=%s name=%s" % (id(self),self.name))
        self.action(self.frame.getCurrentViewer(),state,pos)
        self.frame.enableMenu()

    # Set up a new keyboard callback to also pass the viewer
    def __call__(self, evt, number=None):
        self.dprint("%s called by keybindings" % self)
        self.action(self.frame.getCurrentViewer())


#### Icons

class IconStorage(debugmixin):
    def __init__(self):
        self.il=wx.ImageList(16,16)
        self.map={}

    def get(self,filename):
        if filename not in self.map:
            img=wx.ImageFromBitmap(wx.Bitmap(filename))
            img.Rescale(16,16)
            bitmap=wx.BitmapFromImage(img)
            icon=self.il.Add(bitmap)
            self.dprint("ICON=%s" % str(icon))
            self.dprint(img)
            self.map[filename]=icon
        else:
            self.dprint("ICON: found icon for %s = %d" % (filename,self.map[filename]))
        return self.map[filename]

    def assign(self,notebook):
        # Don't use AssignImageList because the notebook takes
        # ownership of the image list and will delete it when the
        # notebook is deleted.  We're sharing the list, so we don't
        # want the notebook to delete it if the notebook itself
        # deletes it.
        notebook.SetImageList(self.il)

_iconStorage=None
def getIconStorage(icon=None):
    global _iconStorage
    if _iconStorage==None:
        _iconStorage=IconStorage()
    if icon:
        return _iconStorage.get(icon)
    else:
        return _iconStorage




#### View base class

class View(debugmixin,ConfigMixin):
    pluginkey = '-none-'
    icon='icons/page_white.png'
    keyword='Unknown'
    filter=TextFilter()
    temporary=False # True if it is a temporary view
    
    def __init__(self,buffer,frame):
        self.win=None
        self.stc=BlankSTC
        self.buffer=buffer
        self.frame=frame
        self.popup=None

        # View settings.
        ConfigMixin.__init__(self,frame.app.cfg)

    # If there is no title, return the keyword
    def getTitle(self):
        return self.keyword

    def getTabName(self):
        return self.buffer.getTabName()

    def getIcon(self):
        return getIconStorage(self.icon)
    
    def createWindow(self,parent):
        self.win=wx.Window(parent, -1)
        self.win.SetBackgroundColour(wx.ColorRGB(0xabcdef))
        self.stc=stc.StyledTextCtrl(parent,-1)
        self.stc.Show(False)
        #wx.StaticText(self.win, -1, self.buffer.name, (10,10))

    def reparent(self,parent):
        self.win.Reparent(parent)

    def addPopup(self,popup):
        self.popup=popup
        self.win.Bind(wx.EVT_RIGHT_DOWN, self.popupMenu)

    def popupMenu(self,evt):
        # popups generate menu events as normal menus, so the
        # callbacks to the command objects should still work if the
        # popup is generated in the same way that the regular menu and
        # toolbars are.
        self.dprint("popping up menu for %s" % evt.GetEventObject())
        self.win.PopupMenu(self.popup)
        evt.Skip()

    def openPostHook(self):
        pass

    def open(self):
        self.dprint("View: open docptr=%s" % self.buffer.docptr)
        if self.buffer.docptr:
            # the docptr is a real STC, so use it as the base document
            # for the new view
            self.stc.AddRefDocument(self.buffer.docptr)
            self.stc.SetDocPointer(self.buffer.docptr)
        self.openPostHook()

    def close(self):
        self.dprint("View: closing view of buffer %s" % self.buffer)
        #self.stc.ReleaseDocument(self.buffer.docptr)
        self.win.Destroy()
        # remove reference to this view in the buffer's listeners
        self.buffer.remove(self)
        pass

    def focus(self):
        #self.dprint("View: setting focus to %s" % self)
        self.win.SetFocus()

    def showModified(self,modified):
        self.frame.showModified(self)



if __name__ == "__main__":
    pass

