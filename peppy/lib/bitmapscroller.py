# peppy Copyright (c) 2006-2007 Rob McMullen
# Licenced under the GPL; see http://www.flipturn.org/peppy for more info
"""Scrolling Bitmap viewer.

This control is designed to be a generic bitmap viewer that can scroll
to handle large images.

Coordinate systems:

Event coords are in terms of a fixed viewport the size of the client
area of the window containing the scrolled window.  The scrolled
window itself has an origin that can be negative relative to this
viewport.

World coordinates are in terms of the size of the scrolled window
itself, not the smaller size of the viewport onto the scrolled window.
"""

import os

import wx
from wx.lib import imageutils
import wx.lib.newevent

try:
    from peppy.debug import *
except:
    def dprint(txt):
        print txt


##### - Here are some utility functions from wx.lib.mixins.rubberband

def normalizeBox(box):
    """
    Convert any negative measurements in the current
    box to positive, and adjust the origin.
    """
    x, y, w, h = box
    if w < 0:
        x += (w+1)
        w *= -1
    if h < 0:
        y += (h+1)
        h *= -1
    return (x, y, w, h)


def boxToExtent(box):
    """
    Convert a box specification to an extent specification.
    I put this into a seperate function after I realized that
    I had been implementing it wrong in several places.
    """
    b = normalizeBox(box)
    return (b[0], b[1], b[0]+b[2]-1, b[1]+b[3]-1)


def pointInBox(x, y, box):
    """
    Return True if the given point is contained in the box.
    """
    e = boxToExtent(box)
    state = x >= e[0] and x <= e[2] and y >= e[1] and y <= e[3]
    dprint("x=%d y=%d box=%s state=%s" % (x, y, e, state))
    return state


def pointOnBox(x, y, box, thickness=1):
    """
    Return True if the point is on the outside edge
    of the box.  The thickness defines how thick the
    edge should be.  This is necessary for HCI reasons:
    For example, it's normally very difficult for a user
    to manuever the mouse onto a one pixel border.
    """
    outerBox = box
    innerBox = (box[0]+thickness, box[1]+thickness, box[2]-(thickness*2), box[3]-(thickness*2))
    return pointInBox(x, y, outerBox) and not pointInBox(x, y, innerBox)


class MouseSelector(object):
    cursor = wx.CURSOR_ARROW
    
    def __init__(self, scroller, ev=None):
        self.scroller = scroller
        self.world_coords = None
        self.start_img_coords = None
        self.last_img_coords = None

        # cursor stuff
        self.blank_cursor = False

        if ev is not None:
            self.startEvent(ev)

    @classmethod
    def trigger(self, ev):
        """Identify the trigger event to turn on this selector.

        Return True if the event passed in is the event that triggers
        this selector to begin.
        """
        if ev.LeftDown():
            return True
        return False

    def processEvent(self, ev):
        """Process a mouse event for this selector.

        This callback is called for any mouse event once the selector
        is active in the scroller.  The selector is not deactivated
        until this handler returns False, so make sure some event will
        cause it to return False.
        """
        if ev.LeftIsDown() and ev.Dragging():
            self.handleEvent(ev)
        elif ev.LeftUp():
            self.finishEvent(ev)
            return False
        return True
        
    def startEvent(self, ev):
        coords = self.scroller.convertEventCoords(ev)
        self.start_img_coords = self.scroller.getImageCoords(*coords)
        self.setWorldCoordsFromImageCoords(*self.start_img_coords)
        self.draw()
        self.handleEventPostHook(ev)
        dprint("ev: (%d,%d), coords: (%d,%d)" % (ev.GetX(), ev.GetY(), coords[0], coords[1]))
        if self.blank_cursor:
            self.scroller.blankCursor(ev, coords)
        
    def handleEvent(self, ev):
        # draw crosshair (note: in event coords, not converted coords)
        coords = self.scroller.convertEventCoords(ev)
        img_coords = self.scroller.getImageCoords(*coords)
        if img_coords != self.last_img_coords:
            self.erase()
            self.setWorldCoordsFromImageCoords(*img_coords)
            self.draw()
            self.handleEventPostHook(ev)
        #dprint("ev: (%d,%d), coords: (%d,%d)" % (ev.GetX(), ev.GetY(), coords[0], coords[1]))
        if self.blank_cursor:
            self.scroller.blankCursor(ev, coords)
        
    def handleEventPostHook(self, ev):
        pass

    def finishEvent(self, ev):
        self.erase()

    def draw(self):
        if self.start_img_coords:
            dc=self.getXORDC()
            self.drawSelector(dc)

    def recalc(self):
        self.setWorldCoordsFromImageCoords(*self.last_img_coords)

    def erase(self):
        self.draw()
        self.world_coords = None
    
    def getXORDC(self, dc=None):
        if dc is None:
            dc=wx.ClientDC(self.scroller)
        dc.SetPen(wx.Pen(wx.WHITE, 1, wx.DOT))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.SetLogicalFunction(wx.XOR)
        return dc

    def getViewOffset(self):
        xView, yView = self.scroller.GetViewStart()
        xDelta, yDelta = self.scroller.GetScrollPixelsPerUnit()
        xoff = xView * xDelta
        yoff = yView * yDelta
        return -xoff, -yoff
    
    def setWorldCoordsFromImageCoords(self, x, y):
        self.last_img_coords = (x, y)
        zoom = self.scroller.zoom
        offset = int(zoom / 2)
        x = int(x * zoom)
        y = int(y * zoom)
        self.world_coords = (x + offset, y + offset)


class NullSelector(MouseSelector):
    def startEvent(self, ev):
        pass
        
    def handleEvent(self, ev):
        pass

    def draw(self):
        pass
    
# create a new Event class and a EVT binder function for a crosshair
# motion event
(CrosshairMotionEvent, EVT_CROSSHAIR_MOTION) = wx.lib.newevent.NewEvent()

class Crosshair(MouseSelector):
    def __init__(self, scroller, ev=None):
        MouseSelector.__init__(self, scroller)

        self.blank_cursor = True
        self.crossbox = None

        if ev is not None:
            self.startEvent(ev)
        
    def handleEventPostHook(self, ev):
        wx.PostEvent(self.scroller, CrosshairMotionEvent())

    def drawSelector(self, dc):
        xoff, yoff = self.getViewOffset()
        x = self.world_coords[0] + xoff
        y = self.world_coords[1] + yoff
        if self.crossbox:
            dc.DrawRectangle(self.crossbox[0] + xoff, self.crossbox[1] + yoff,
                             self.crossbox[2], self.crossbox[3])
            dc.DrawLine(x, 0, x,
                        self.crossbox[1] + yoff)
            dc.DrawLine(x, self.crossbox[1] + self.crossbox[3] + yoff + 1,
                        x, self.scroller.height)
            dc.DrawLine(0, y,
                        self.crossbox[0] + xoff, y)
            dc.DrawLine(self.crossbox[0] + self.crossbox[2] + xoff + 1, y,
                        self.scroller.width, y)
        else:
            dc.DrawLine(x, 0, x, self.scroller.height)
            dc.DrawLine(0, y, self.scroller.width, y)

    def setWorldCoordsFromImageCoords(self, x, y):
        self.last_img_coords = (x, y)
        zoom = self.scroller.zoom
        offset = int(zoom / 2)
        x = int(x * zoom)
        y = int(y * zoom)
        self.world_coords = (x + offset, y + offset)
        if self.scroller.zoom >= 1:
            self.crossbox = (x-1, y-1, zoom + 2, zoom + 2)
        else:
            self.crossbox = None
        dprint("crosshair = %s, img = %s" % (self.world_coords, self.last_img_coords))


# create a new Event class and a EVT binder function for a crosshair
# motion event
(RubberBandMotionEvent, EVT_RUBBERBAND_MOTION) = wx.lib.newevent.NewEvent()

class RubberBand(MouseSelector):
    move_cursor = wx.CURSOR_SIZING
    resize_cursors = [wx.CURSOR_SIZENWSE,
                      wx.CURSOR_SIZENS,
                      wx.CURSOR_SIZENESW,
                      wx.CURSOR_SIZEWE,
                      wx.CURSOR_SIZENWSE,
                      wx.CURSOR_SIZENS,
                      wx.CURSOR_SIZENESW,
                      wx.CURSOR_SIZEWE
                      ]
    
    def __init__(self, scroller, ev=None):
        MouseSelector.__init__(self, scroller)

        self.border_sensitivity = 3
        self.resize_index = None
        self.event_type = None
        self.move_img_coords = None

        if ev is not None:
            self.startEvent(ev)

    def processEvent(self, ev):
        """Process a mouse event for this selector.

        This callback is called for any mouse event once the selector
        is active in the scroller.  The selector is not deactivated
        until this handler returns False, so make sure some event will
        cause it to return False.
        """
        if ev.LeftDown():
            # restart event if we get another LeftDown
            self.startEvent(ev)
        elif ev.LeftIsDown() and ev.Dragging():
            self.handleEvent(ev)
        elif ev.LeftUp():
            self.finishEvent(ev)
        elif ev.Moving():
            # no mouse buttons; change cursor if over resize box
            self.handleCursorChanges(ev)
        return True
        
    def startEvent(self, ev):
        """Driver for new event.

        This selector recognizes a few different types of events: a
        normal event where the user uses the mouse to select a new
        rectangular area, a move event where the user can drag around
        the area without changing its size, and a bunch of resize
        events where the user can grab a corner or edge and make the
        rectangular area bigger.
        """
        coords = self.scroller.convertEventCoords(ev)
        dprint("mouse=%s world=%s" % (coords, self.world_coords))
        if self.isOnBorder(coords):
            self.startResizeEvent(ev, coords)
        elif self.isInside(coords):
            self.startMoveEvent(ev, coords)
        else:
            self.startNormalEvent(ev, coords)

    def startNormalEvent(self, ev, coords):
        self.event_type = None
        self.erase()
        self.start_img_coords = self.scroller.getImageCoords(*coords)
        self.setWorldCoordsFromImageCoords(*self.start_img_coords)
        self.draw()
        self.handleEventPostHook(ev)
        dprint("ev: (%d,%d), coords: (%d,%d)" % (ev.GetX(), ev.GetY(), coords[0], coords[1]))
        if self.blank_cursor:
            self.scroller.blankCursor(ev, coords)

    def startResizeEvent(self, ev, coords):
        self.event_type = "resize"
        dprint(self.event_type)

    def startMoveEvent(self, ev, coords):
        self.event_type = "move"
        self.normalizeImageCoords()
        self.move_img_coords = self.scroller.getImageCoords(*coords)
        dprint("%s: starting from %s" % (self.event_type, self.move_img_coords))
        
    def handleEvent(self, ev):
        if self.event_type == "resize":
            self.handleResizeEvent(ev)
        elif self.event_type == "move":
            self.handleMoveEvent(ev)
        else:
            MouseSelector.handleEvent(self, ev)
        dprint(self.world_coords)

    def handleResizeEvent(self, ev):
        dprint()
        
    def handleMoveEvent(self, ev):
        dprint()
        coords = self.scroller.convertEventCoords(ev)
        img_coords = self.scroller.getImageCoords(*coords)
        if img_coords != self.move_img_coords:
            self.erase()
            self.moveWorldCoordsFromImageCoords(*img_coords)
            self.draw()
            self.handleEventPostHook(ev)
        
    def handleCursorChanges(self, ev):
        coords = self.scroller.convertEventCoords(ev)
        dprint("mouse=%s world=%s" % (coords, self.world_coords))
        self.resize_index = None
        if self.isOnBorder(coords):
            self.resize_index = self.getBorderCursorIndex(coords)
            cursor = self.resize_cursors[self.resize_index]
        elif self.isInside(coords):
            cursor = self.move_cursor
        else:
            cursor = self.cursor
        self.scroller.setCursor(cursor)

    def finishEvent(self, ev):
        dprint()
        pass

    def handleEventPostHook(self, ev):
        wx.PostEvent(self.scroller, RubberBandMotionEvent())

    def getXORDC(self, dc=None):
        if dc is None:
            dc=wx.ClientDC(self.scroller)
        dc.SetPen(wx.Pen(wx.WHITE, 1, wx.DOT))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.SetLogicalFunction(wx.XOR)
        return dc

    def drawSelector(self, dc):
        xoff, yoff = self.getViewOffset()
        x = self.world_coords[0] + xoff
        y = self.world_coords[1] + yoff
        w = self.world_coords[2]
        h = self.world_coords[3]

        dprint("start=%s current=%s  xywh=%s" % (self.start_img_coords,
                                                 self.last_img_coords, (x,y,w,h)))
        dc.DrawRectangle(x, y, w, h)

    def isOnBorder(self, coords):
        """Are the world coordinates on the selection border?

        Return true if the world coordinates specified are on or
        within a tolerance of the selecton border.
        """
        dprint(self.world_coords)
        if self.world_coords is None:
            return False
        return pointOnBox(coords[0], coords[1], self.world_coords, self.border_sensitivity)

    def getBorderCursorIndex(self, coords):
        """Get resize cursor depending on position on border.

        Modified from wx.lib.mixins.rubberband: Return a position
        number in the range 0 .. 7 to indicate where on the box border
        the point is.  The layout is:

              0    1    2
              7         3
              6    5    4
        """
        x0, y0, x1, y1 = boxToExtent(self.world_coords)
        x = coords[0]
        y = coords[1]
        t = self.border_sensitivity
        if x >= x0-t and x <= x0+t:
            # O, 7, or 6
            if y >= y0-t and y <= y0+t:
                index = 0
            elif y >= y1-t and y <= y1+t:
                index = 6
            else:
                index = 7
        elif x >= x1-t and x <= x1+t:
            # 2, 3, or 4
            if y >= y0-t and y <= y0+t:
                index = 2
            elif y >= y1-t and y <= y1+t:
                index = 4
            else:
                index = 3
        elif y >= y0-t and y <= y0+t:
            index = 1
        else:
            index = 5
        return index

    def isInside(self, coords):
        """Are the world coordinates on the selection border?

        Return true if the world coordinates specified are on or
        within a tolerance of the selecton border.
        """
        dprint(self.world_coords)
        if self.world_coords is None:
            return False
        return pointInBox(coords[0], coords[1], self.world_coords)

    def recalc(self):
        zoom = self.scroller.zoom
        x, y = self.last_img_coords
        xi, yi = self.start_img_coords
        if x > xi:
            x0 = int(xi * zoom)
            x1 = int((x+1) * zoom) - 1
        else:
            x0 = int(x * zoom)
            x1 = int((xi+1) * zoom) - 1

        if y > yi:
            y0 = int(yi * zoom)
            y1 = int((y+1) * zoom) - 1
        else:
            y0 = int(y * zoom)
            y1 = int((yi+1) * zoom) - 1

        # (x0, y0) and (x1, y1) always point within the pixel, so we
        # need to expand the area so that the highlighted area is
        # outside the selection region.
        self.world_coords = (x0 - 1, y0 - 1, x1 - x0 + 2, y1 - y0 + 2)

    def normalizeImageCoords(self):
        x0, y0 = self.start_img_coords
        x1, y1 = self.last_img_coords
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        self.start_img_coords = (x0, y0)
        self.last_img_coords = (x1, y1)

    def setWorldCoordsFromImageCoords(self, x, y):
        self.last_img_coords = (x, y)
        self.recalc()

    def moveWorldCoordsFromImageCoords(self, x, y):
        dx = x - self.move_img_coords[0]
        dy = y - self.move_img_coords[1]
        self.move_img_coords = (x, y)
        x0 = self.start_img_coords[0]
        y0 = self.start_img_coords[1]
        x1 = self.last_img_coords[0]
        y1 = self.last_img_coords[1]
        if x0 + dx < 0:
            dx = -x0
        elif x1 + dx >= self.scroller.img.GetWidth():
            dx = self.scroller.img.GetWidth() - x1 - 1
        if y0 + dy < 0:
            dy = -y0
        elif y1 + dy >= self.scroller.img.GetHeight():
            dy = self.scroller.img.GetHeight() - y1 - 1
        self.start_img_coords = (x0 + dx, y0 + dy)
        self.last_img_coords = (x1 + dx, y1 + dy)
        self.recalc()


class BitmapScroller(wx.ScrolledWindow):
    def __init__(self, parent, selector=RubberBand):
        wx.ScrolledWindow.__init__(self, parent, -1)

        # Settings
        self.background_color = wx.Colour(160, 160, 160)
        self.use_checkerboard = True
        self.checkerboard_box_size = 8
        self.checkerboard_color = wx.Colour(96, 96, 96)
        self.max_zoom = 16.0
        self.min_zoom = 0.0625

        # internal storage
        self.orig_img = None
        self.img = None
        self.scaled_bmp = None
        self.width = 0
        self.height = 0
        self.zoom = 4.0
        self.crop = None

        # cursors
        self.default_cursor = wx.CURSOR_ARROW
        self.save_cursor = None
        
        self.Bind(wx.EVT_PAINT, self.OnPaint)

        self.use_selector = None
        self.selector = None
        self.selector_event_callback = None
        self.Bind(wx.EVT_MOUSE_EVENTS, self.OnMouseEvent)
        self.Bind(wx.EVT_KILL_FOCUS, self.OnKillFocus)

        self.setSelector(selector)

    def zoomIn(self, zoom=2):
        self.zoom *= zoom
        if self.zoom > self.max_zoom:
            self.zoom = self.max_zoom
        self.scaleImage()
        
    def zoomOut(self, zoom=2):
        self.zoom /= zoom
        if self.zoom < self.min_zoom:
            self.zoom = self.min_zoom
        self.scaleImage()

    def clearBackground(self, dc, w, h):
        dc.SetBackground(wx.Brush(self.background_color))
        dc.Clear()

    def checkerboardBackground(self, dc, w, h):
        # draw checkerboard for transparent background
        box = self.checkerboard_box_size
        y = 0
        while y < h:
            #dprint("y=%d, y/box=%d" % (y, (y/box)%2))
            x = box * ((y/box)%2)
            while x < w:
                dc.SetPen(wx.Pen(self.checkerboard_color))
                dc.SetBrush(wx.Brush(self.checkerboard_color))
                dc.DrawRectangle(x, y, box, box)
                #dprint("draw: xywh=%s" % ((x, y, box, box),))
                x += box*2
            y += box

    def drawBackground(self, dc, w, h):
        self.clearBackground(dc, w, h)
        if self.use_checkerboard:
            self.checkerboardBackground(dc, w, h)

    def inOrigImage(self, x, y):
        if x>=0 and x<self.orig_img.GetWidth() and y>=0 and y<self.orig_img.GetHeight():
            return True
        return False

    def getCroppedImage(self):
        if self.crop is not None and isinstance(self.crop, tuple):
            if self.inOrigImage(self.crop[0], self.crop[1]) and self.inOrigImage(self.crop[0] + self.crop[2] - 1, self.crop[1] + self.crop[3] - 1):
                return self.orig_img.GetSubImage(self.crop)
            else:
                print("trying to crop outside of image: %s" % str(self.crop))
        return self.orig_img

    def scaleImage(self):
        if self.orig_img is not None:
            self.img = self.getCroppedImage()
            w = int(self.img.GetWidth() * self.zoom)
            h = int(self.img.GetHeight() * self.zoom)
            dc = wx.MemoryDC()
            self.scaled_bmp = wx.EmptyBitmap(w, h)
            dc.SelectObject(self.scaled_bmp)
            self.drawBackground(dc, w, h)
            dc.DrawBitmap(wx.BitmapFromImage(self.img.Scale(w, h)), 0,0, True)
            self.width = self.scaled_bmp.GetWidth()
            self.height = self.scaled_bmp.GetHeight()           
        else:
            self.width = 10
            self.height = 10
        self.SetVirtualSize((self.width, self.height))
        rate = int(self.zoom)
        if rate < 1:
            rate = 1
        self.SetScrollRate(rate, rate)
        self.Refresh()
        
    def setImage(self, img=None, zoom=None, rot=None,
                 vmirror=False, hmirror=False, crop=None):
        if img is not None:
            # change the bitmap if specified
            self.bmp = None
            self.orig_img = img
        else:
            self.bmp = self.orig_img = None

        if zoom is not None:
            self.zoom = zoom

        self.crop = crop

        self.scaleImage()

    def setBitmap(self, bmp=None, zoom=None):
        if bmp is not None:
            img = bmp.ConvertToImage()
            self.setImage(img, zoom)
        else:
            self.setImage(None, zoom)

    def copyToClipboard(self):
        bmpdo = wx.BitmapDataObject(self.scaled_bmp)
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(bmpdo)
            wx.TheClipboard.Close()

    def convertEventCoords(self, ev):
        """Convert event coordinates to world coordinates.

        Convert the event coordinates to world coordinates by locating
        the offset of the scrolled window's viewport and adjusting the
        event coordinates.
        """
        xView, yView = self.GetViewStart()
        xDelta, yDelta = self.GetScrollPixelsPerUnit()
        x = ev.GetX() + (xView * xDelta)
        y = ev.GetY() + (yView * yDelta)
        return (x, y)

    def getImageCoords(self, x, y, fixbounds = True):
        """Convert scrolled window coordinates to image coordinates.

        Convert from the scrolled window coordinates (where (0,0) is
        the upper left corner when the window is scrolled to the
        top-leftmost position) to the corresponding point on the
        original (i.e. unzoomed, unrotated, uncropped) image.
        """
        x = int(x / self.zoom)
        y = int(y / self.zoom)
        if fixbounds:
            if x<0: x=0
            elif x>=self.img.GetWidth(): x=self.img.GetWidth()-1
            if y<0: y=0
            elif y>=self.img.GetHeight(): y=self.img.GetHeight()-1
        return (x, y)

    def isInBounds(self, x, y):
        """Check if world coordinates are on the image.

        Return True if the world coordinates lie on the image.
        """
        if self.img is None or x<0 or y<0 or x>=self.width or y>=self.height:
            return False
        return True

    def isEventInClientArea(self, ev):
        """Check if event is in the viewport.

        Return True if the event is within the visible viewport of the
        scrolled window.
        """
        size = self.GetClientSizeTuple()
        x = ev.GetX()
        y = ev.GetY()
        if x < 0 or x >= size[0] or y < 0 or y >= size[1]:
            return False
        return True

    def setCursor(self, cursor):
        """Set cursor for the window.

        A mild enhancement of the wx standard SetCursor that takes an
        integer id as well as a wx.StockCursor instance.
        """
        if isinstance(cursor, int):
            cursor = wx.StockCursor(cursor)
        self.SetCursor(cursor)

    def blankCursor(self, ev, coords=None):
        dprint()
        if coords is None:
            coords = self.convertEventCoords(ev)
        if self.isInBounds(*coords) and self.isEventInClientArea(ev):
            if not self.save_cursor:
                self.save_cursor = True
                self.setCursor(wx.StockCursor(wx.CURSOR_BLANK))
        else:
            if self.save_cursor:
                self.setCursor(self.use_selector.cursor)
                self.save_cursor = False

    def getSelectorCoordsOnImage(self, with_cropped_offset=True):
        if self.selector:
            x, y = self.getImageCoords(*self.selector.world_coords)
            if self.crop is not None and with_cropped_offset:
                x += self.crop[0]
                y += self.crop[1]
            return (x, y)
        return None
    
    #### - Automatic scrolling

    def autoScroll(self, ev):
        x = ev.GetX()
        y = ev.GetY()
        size = self.GetClientSizeTuple()
        if x < 0:
            dx = x
        elif x > size[0]:
            dx = x - size[0]
        else:
            dx = 0
        if y < 0:
            dy = y
        elif y > size[1]:
            dy = y - size[1]
        else:
            dy = 0
        wx.CallAfter(self.autoScrollCallback, dx, dy)

    def autoScrollCallback(self, dx, dy):
        spx = self.GetScrollPos(wx.HORIZONTAL)
        spy = self.GetScrollPos(wx.VERTICAL)
        if self.selector:
            self.selector.erase()
        self.Scroll(spx+dx, spy+dy)
        if self.selector:
            self.selector.recalc()
            self.selector.draw()

    def setSelector(self, selector):
        if self.selector:
            self.selector = None
        self.use_selector = selector
        self.setCursor(self.use_selector.cursor)
        self.save_cursor = None

    def OnMouseEvent(self, ev):
        if self.img:
            if self.selector:
                if not self.selector.processEvent(ev):
                    self.selector = None
                    if self.save_cursor:
                        self.setCursor(self.use_selector.cursor)
                        self.save_cursor = None
                elif not self.isEventInClientArea(ev):
                    self.autoScroll(ev)
            elif self.use_selector.trigger(ev):
                self.selector = self.use_selector(self, ev)

    def OnKillFocus(self, evt):
        if self.selector:
            self.selector.erase()
            self.selector = None

    def OnPaint(self, evt):
        if self.scaled_bmp is not None:
            dc=wx.BufferedPaintDC(self, self.scaled_bmp, wx.BUFFER_VIRTUAL_AREA)
            # Note that the drawing actually happens when the dc goes
            # out of scope and is destroyed.
            self.OnPaintHook(evt, dc)
        evt.Skip()

    def OnPaintHook(self, evt, dc):
        """Hook to draw any additional items onto the saved bitmap.

        Note that any changes made to the dc will be reflected in the
        saved bitmap, so subsequent times calling this function will
        continue to add new data to the image.
        """
        pass


if __name__ == '__main__':
    app   = wx.PySimpleApp()
    frame = wx.Frame(None, -1, title='BitmapScroller Test', size=(500,500))

    # Add a panel that the rubberband will work on.
    panel = BitmapScroller(frame)
    img = wx.ImageFromBitmap(wx.ArtProvider_GetBitmap(wx.ART_WARNING, wx.ART_OTHER, wx.Size(48, 48)))
    panel.setImage(img)

    # Layout the frame
    sizer = wx.BoxSizer(wx.VERTICAL)
    sizer.Add(panel,  1, wx.EXPAND | wx.ALL, 5)

    buttonsizer = wx.BoxSizer(wx.HORIZONTAL)
    sizer.Add(buttonsizer, 0, wx.EXPAND | wx.ALL, 5)
    
    def buttonHandler(ev):
        id = ev.GetId()
        if id == 100:
            panel.zoomIn()
        elif id == 101:
            panel.zoomOut()
        elif id == 102:
            panel.setSelector(Crosshair)
        elif id == 103:
            panel.setSelector(RubberBand)
    button = wx.Button(frame, 100, 'Zoom In')
    frame.Bind(wx.EVT_BUTTON, buttonHandler, button)
    buttonsizer.Add(button, 0, wx.EXPAND, 0)
    button = wx.Button(frame, 101, 'Zoom Out')
    frame.Bind(wx.EVT_BUTTON, buttonHandler, button)
    buttonsizer.Add(button, 0, wx.EXPAND, 0)
    button = wx.Button(frame, 102, 'Crosshair')
    frame.Bind(wx.EVT_BUTTON, buttonHandler, button)
    buttonsizer.Add(button, 0, wx.EXPAND, 0)
    button = wx.Button(frame, 103, 'Select')
    frame.Bind(wx.EVT_BUTTON, buttonHandler, button)
    buttonsizer.Add(button, 0, wx.EXPAND, 0)

    frame.SetAutoLayout(1)
    frame.SetSizer(sizer)
    frame.Show(1)
    app.MainLoop()
