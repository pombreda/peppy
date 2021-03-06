#-----------------------------------------------------------------------------
# Name:        userparams.py
# Purpose:     class attribute preferences and serialization
#
# Author:      Rob McMullen
#
# Created:     2007
# RCS-ID:      $Id: $
# Copyright:   (c) 2007 Rob McMullen
# License:     wxWidgets
#-----------------------------------------------------------------------------
"""Helpers to create user preferences for class attribute defaults and
editing widgets for user modification of preferences.

This module is used to create preferences that can be easily saved to
configuration files.  It is designed to be class-based, not instance
based.

Classes need to inherit from ClassPrefs and then define a class
attribute called default_classprefs that is a tuple of Param objects.
Subclasses will inherit the preferences of their parent classes, and
can either redefine the defaults or add new parameters.  For example:

  class Vehicle(ClassPrefs):
      default_classprefs = (
          IntParam('wheels', 0, 'Number of wheels on the vehicle'),
          IntParam('doors', 0, 'Number of doors on the vehicle'),
          BoolParam('operational', True, 'Is it running?'),
          BoolParam('engine', False, 'Does it have a motor?'),
          )

  class Car(Vehicle):
      default_classprefs = (
          IntParam('wheels', 4),
          IntParam('doors', 4),
          BoolParam('engine', True),
          StrParam('license_state', '', 'State in which registered'),
          StrParam('license_plate', '', 'License plate number'),
          )

  class WxCar(wx.Window, Car):
      default_classprefs = (
          IntParam('pixel_height', 400),
          IntParam('pixel_width', 600),
          )

The metaclass for ClassPrefs processes the default_classprefs and adds
another class attribute called classprefs that is a proxy object into
a global preferences object.

The global preferences object GlobalPrefs uses the ConfigParser module
to serialize and unserialize user preferences.  Since the user
preferences are stored in text files, the Param objects turn the user
text into the expected type of the Param so that your python code only
has to deal with the expected type and doesn't have to do any conversion
itself.

The user configuration for the above example could look like this:

  [Vehicle]
  operational = False
  
  [Car]
  license_state = CA
  doors = 2
  
  [WxCar]
  pixel_width = 300
  pixel_height = 200

and the GlobalPrefs.readConfig method will parse the file,
interpreting the section name as the class.  It will set
Vehicle.classprefs.operational to be the boolean value False,
Car.classprefs.license_state to the string 'CA', Car.classprefs.doors to
the integer value 2, etc.

Your code doesn't need to know anything about the conversion from the
string to the expected type -- it's all handled by the ClassPrefs and
GlobalPrefs.

The PrefDialog class provides the user interface dialog to modify
class parameters.  It creates editing widgets that correspond to the
Param class -- for BoolParam it creates a checkbox, IntParam a text
entry widget, ChoiceParam a pulldown list, etc.  The help text is
displayed as a tooltip over the editing widget.  User subclasses of
Param are possible as well.

"""

import os, sys, struct, time, re, types, copy
from cStringIO import StringIO
from ConfigParser import ConfigParser
import locale

import wx
import wx.stc
from wx.lib.pubsub import Publisher
from wx.lib.evtmgr import eventManager
from wx.lib.scrolledpanel import ScrolledPanel
from wx.lib.stattext import GenStaticText

from peppy.lib.column_autosize import ColumnAutoSizeMixin
from peppy.lib.controls import FileBrowseButton2, DirBrowseButton2, FontBrowseButton

try:
    from peppy.debug import *
except:
    def dprint(txt=""):
        print txt
        return True

    class debugmixin(object):
        debuglevel = 0
        def dprint(self, txt):
            if self.debuglevel > 0:
                dprint(txt)
            return True


class Param(debugmixin):
    """Generic param interface.

    Param objects follow the lifetime of the class, and so are not
    typically destroyed until the end of the program.  That also means
    that they operate as flyweight objects with their state stored
    extrinsically.  They are also factories for creating editing
    widgets (the getCtrl method) and as visitors to process the
    conversion between the user interface representation of the value
    and the user code's representation of the value (the get/setValue
    methods).

    It's important to understand the two representations of the param.
    What I call "text" is the textual representation that is stored in
    the user configuration file, and what I call "value" is the result
    of the conversion into the correct python type.  The value is what
    the python code operates on, and doesn't need to know anything about
    the textual representation.  These conversions are handled by the
    textToValue and valueToText methods.

    Note that depending on the wx control used to display the value,
    additional conversion may be necessary to show the value.  This is
    handled by the getValue and setValue methods.

    The default Param is a string param, and no restriction on the
    value of the string is imposed.
    """

    # class default to be used as the instance default if no other
    # default is provided.
    default = None
    
    # Event to be used as the callback trigger when the user changes the
    # control.  Subclasses should override this class attribute to correspond
    # to whatever event is appropriate for the control
    callback_event = wx.EVT_TEXT
    
    def __init__(self, keyword, default=None, help='',
                 save_to_file=True, **kwargs):
        self.keyword = keyword
        if default is not None:
            self.default = default
        self.help = help
        self.save_to_file = save_to_file
        
        # join means to place the next param's control on the same line as
        # this one
        if 'next_on_same_line' in kwargs or 'join' in kwargs:
            self.next_on_same_line = True
        else:
            self.next_on_same_line = False
        
        # wide params expand to fill extra columns; fullwidth params expand to
        # fill the width of the window
        if 'fullwidth' in kwargs:
            self.fullwidth = kwargs['fullwidth']
        else:
            self.fullwidth = False
        if 'wide' in kwargs:
            self.wide = kwargs['wide']
        else:
            self.wide = self.fullwidth
        
        # An alternate label can be supplied using the keyword 'label'
        if 'label' in kwargs:
            self.alt_label = kwargs['label']
        else:
            self.alt_label = None
        
        # The editing control of the param can also be disabled
        if 'disable' in kwargs:
            self.enable = not kwargs['disable']
        else:
            self.enable = True
        
        # The control can also be hidden entirely from the UI
        if 'hidden' in kwargs:
            self.hidden = kwargs['hidden']
        else:
            self.hidden = False
        
        # Params marked as 'local' will be placed in the instance's local
        # namespace as well as in the classprefs.  This means that the
        # instance can have different values for the local version of the
        # param as compared to the class-wide version.
        if 'local' in kwargs:
            self.local = kwargs['local']
        else:
            self.local = False
        
        if 'override_superclass' in kwargs:
            self.override_superclass = kwargs['override_superclass']
        else:
            self.override_superclass = False
        
        # User options are anything leftover in the kwargs dict.
        self.user_options = dict(kwargs)
        
        # Flag to indicate if any callbacks should be processed.  When the
        # value has been changed programmatically (i.e.  not by the user typing
        # something), it may be desirable to turn off callbacks.  This happens
        # if some params dynamically depend on the values in other params, and
        # could lead to a race condition if callbacks are allowed to propagate
        self.user_initiated_callback = True
        
        # Added a user settable callback hook.  Use setObserverCallback
        self.processCallbackHook = None
    
    def __str__(self):
        return "keyword=%s, default=%s, next=%s, show=%s, help=%s" % (self.keyword,
        self.default, self.next_on_same_line, self.alt_label, self.help)
    
    def isIterableDataType(self):
        """Returns whether the value used by this classpref is a list, set,
        hash or other datatype that can be updated internally without changing
        the reference to it.
        
        For lists, sets, and hashes, the value of the contents of the
        classpref can change without updating the classpref itself.  It's the
        different between appending to a list where the list pointer doesn't
        change to assigning a new value to the classpref which updates the
        GlobalPrefs.user dict.  So, we force the update here so that the value
        is always checked when writing the config file.
        """
        return False
    
    def setInitialDefaults(self, ctrl, ctrls, getter, orig):
        """Set the initial state of the control and perform any other one-time
        setup.
        
        @param ctrl: the wxControl
        
        @param ctrls: dict of ctrls keyed on param
        
        @param getter: the functor keyed on the param name that returns the
        default value for the item
        
        @param orig: dict keyed on param object that stores the original value
        of the item.  This is used to check to see if the user has made any
        changes to the params.
        """
        if getter is not None:
            val = getter(self.keyword)
            #dprint("keyword %s: val = %s(%s)" % (self.keyword, val, type(val)))
            self.setValue(ctrl, val)
            orig[self.keyword] = val

    def updateIfModified(self, ctrls, orig, setter, locals=None):
        """Update values if the user has changed any items
        
        @param ctrls: dict of ctrls keyed on param
        
        @param orig: dict keyed on param object that contains the original
        value of the item.
        
        @param setter: functor that takes two arguments: the keyword and the
        value to set the value in the user's classpref.
        """
        ctrl = ctrls[self]
        val = self.getValue(ctrl)
        if val != orig[self.keyword]:
            self.dprint("%s has changed from %s(%s) to %s(%s)" % (self.keyword, orig[self.keyword], type(orig[self.keyword]), val, type(val)))
            setter(self.keyword, val)
            if locals and param.local:
                locals[self.keyword] = val

    def isSettable(self):
        """True if the user can set this parameter"""
        return True

    def isVisible(self):
        """True if this item is to be displayed."""
        return not self.hidden
    
    def isSuperseded(self):
        """True if this param should remove the parameter of the same keyword
        from the superclass's user interface.
        
        This is used in L{SupersededParam} to 
        """
        return False

    def isOverridingSuperclass(self):
        """True if this param breaks inheritance so that superclasses don't
        provide defaults for this class.
        """
        return self.override_superclass

    def isDependentKeywordStart(self):
        """True if this param starts a list of dependent keywords
        
        Dependent keywords are those between L{UserListParamStart} and
        L{UserListParamEnd}: the value of the UserListParamStart is used as a
        key into a list to change the value of the dependent keywords.
        """
        return False

    def isDependentKeywordEnd(self):
        """True if this param is the end of the dependent list.
        """
        return False

    def isSectionHeader(self):
        """True if a static box should be started with this param
        """
        return False

    def getStaticBoxTitle(self):
        """Return the text to be used as the name of the static box displayed
        around the controls.
        """
        return ""

    def getLabel(self, parent):
        if self.alt_label == False:
            return None
        
        if self.alt_label is None:
            text = self.keyword
        else:
            text = self.alt_label
        title = GenStaticText(parent, -1, text)
        if self.help:
            title.SetToolTipString(self.help)
        return title

    def getCtrl(self, parent, initial=None):
        """Create and editing control.

        Given the parent window, create a user interface element to
        edit the param.
        """
        ctrl = wx.TextCtrl(parent, -1, size=(125, -1),
                           style=wx.TE_PROCESS_ENTER)
        ctrl.Enable(self.enable)
        return ctrl
    
    def setObserverCallback(self, callback):
        """Sets a callback hook that calls this function when the
        processCallback method is called.
        
        This method provides a way to hook a callback to the param without
        subclassing the param.  This hook will be called at the end of the
        processCallback.  The callback should have the following method
        signature:
        
           def callback(param, evt, ctrl, ctrl_list)
        
        See the definition of L{processCallback} for a description of the
        paramaters that will be passed to the callback.
        """
        self.processCallbackHook = callback
    
    def processCallback(self, evt, ctrl, ctrl_list):
        """Subclasses should override this to provide functionality.
        
        This callback is the class-specific callback in response to the
        event named in the class attribute 'callback_event'.
        
        evt: event object
        ctrl: control from evt.GetEventObject
        ctrl_list: dict mapping param objects to their corresponding controls
        """
        evt.Skip()
    
    def OnCallback(self, evt):
        """Callback driver for the control"""
        ctrl = evt.GetEventObject()
        if self.user_initiated_callback:
            self.processCallback(evt, ctrl, ctrl.ctrl_list)
            if self.processCallbackHook:
                self.processCallbackHook(self, evt, ctrl, ctrl.ctrl_list)
    
    def setCallback(self, ctrl, ctrl_list):
        """Set the callback that responds to changes in the state of the control
        
        This is called during control creation to set the callback in response
        to the class attribute 'callback_event' that is particular to the type
        of control being created.  Subclasses should change the callback_event
        to an event that is fired in response to the user changing the state
        of the control.
        
        Some controls have multiple events that can be fired; for now, only
        a single event is handled.
        """
        if self.callback_event is not None:
            ctrl.ctrl_list = ctrl_list
            ctrl.Bind(self.callback_event, self.OnCallback)
    
    def overrideCallback(self, ctrl, callback):
        """Override the callback on the control.
        
        Force the callback for the control to call the specified function.
        Normally the callback is tied to the param's L{OnCallback} method,
        allowing the param to do checking on the input.  However, the callback
        may be overridden with this method to force an individual control to
        call a specific function.  This means that the callback is no longer
        tied to the param's callback, and the callback will have to determine
        which control was acted upon by using the evt.GetEventObject() method.
        
        Some controls have multiple events that can be fired; for now, only
        a single event is handled.
        """
        if self.callback_event is not None:
            ctrl.Bind(self.callback_event, callback)
    
    def setInitialState(self, ctrl, ctrl_list):
        """Set the initial state of the control.
        
        Callback to set the initial state of the widget based on the state of
        all the other controls in the collection of params.  This must only
        be called after all of the controls in the param collection have
        been created, because the the ctrl_list dict must contain the entire
        mapping of params to controls.
        """
        pass

    def textToValue(self, text):
        """Convert the user's config text to the type expected by the
        python code.

        Subclasses should return the type expected by the user code.
        """
        # The default implementation just returns a string with any
        # delimiting quotation marks removed.
        if text.startswith("'") or text.startswith('"'):
            text = text[1:]
        if text.endswith("'") or text.endswith('"'):
            text = text[:-1]
        return text

    def valueToText(self, value):
        """Convert the user value to a string suitable to be written
        to the config file.

        Subclasses should convert the value to a string that is
        acceptable to textToValue.
        """
        # The default string implementation adds quotation characters
        # to the string.
        if isinstance(value, str):
            value = '"%s"' % value
        return str(value)

    def setValue(self, ctrl, value):
        """Populate the control given the user value.

        If any conversion is needed to show the user value in the
        control, do it here.
        """
        # The default string edit control doesn't need any conversion.
        ctrl.SetValue(value)

    def setValueWithoutCallback(self, ctrl, value):
        """Populate the control given the user value, but don't execute any
        callbacks.

        Callbacks are designed to be executed in response to the user changing
        the value, not to a programmatic change.  If you don't care that
        the callback might be triggered when using the setValue method, then
        there's no problem with the program calling setValue.  However, if
        you've extended a Param to depend on the value of another param; e.g.
        you've created a processCallback or setInitialState that changes
        in response to the values in another param, it is most safe to call
        setValueWithoutCallback for programmatic changes, unless you take care
        to guarantee the order of the setValue calls.
        """
        self.user_initiated_callback = False
        self.setValue(ctrl, value)
        self.user_initiated_callback = True

    def getValue(self, ctrl):
        """Get the user value from the control.

        If the control doesn't automatically return a value of the
        correct type, convert it here.
        """
        # The default string edit control simply returns the string.
        return str(ctrl.GetValue())
    
    def findParamFromKeyword(self, name, ctrls):
        """Utility function to find the param instance from the ctrl_list dict
        
        @param name: keyword of the classpref
        
        @param ctrls: dict of ctrls keyed on param
        """
        for param in ctrls.keys():
            if param.keyword == name:
                return param
        return None

class DeprecatedParam(Param):
    """Parameter that is only around for compatibility purposes and should be
    set through some other means.
    """

    def getCtrl(self, parent, initial=None):
        """Create and editing control.

        Given the parent window, create a user interface element to
        edit the param.
        """
        ctrl = GenStaticText(parent, -1, "")
        ctrl.Enable(self.enable)
        return ctrl

    def setValue(self, ctrl, value):
        """Populate the control given the user value.

        If any conversion is needed to show the user value in the
        control, do it here.
        """
        # The default string edit control doesn't need any conversion.
        ctrl.SetLabel(value)

    def getValue(self, ctrl):
        """Get the user value from the control.

        If the control doesn't automatically return a value of the
        correct type, convert it here.
        """
        # The default string edit control simply returns the string.
        return str(ctrl.GetLabel())

class ReadOnlyParam(debugmixin):
    """Read-only parameter for display only."""
    callback_event = None
    
    def __init__(self, keyword, default=None, help=''):
        self.keyword = keyword
        if default is not None:
            self.default = default
        self.help = ''

    def isIterableDataType(self):
        return False
    
    def isSettable(self):
        return False

    def isSectionHeader(self):
        return False
    
    def isSuperseded(self):
        return False
    
    def isOverridingSuperclass(self):
        return False
    
    def isDependentKeywordStart(self):
        return False


class SupersededParam(ReadOnlyParam):
    """Parameter used to hide a parameter in the superclass's user interface.
    
    """
    callback_event = None
    
    def __init__(self, keyword, default=None, help=''):
        self.keyword = keyword
        self.default = default
        self.help = ''
        self.hidden = True
        self.local = False

    def isVisible(self):
        return False
    
    def isSuperseded(self):
        return True

class ParamSection(ReadOnlyParam):
    def isVisible(self):
        return False

    def isSectionHeader(self):
        return True
    
    def getStaticBoxTitle(self):
        return self.keyword

class BoolParam(Param):
    """Boolean parameter that displays a checkbox as its user interface.

    Text uses one of 'yes', 'true', or '1' to represent the bool True,
    and anything else to represent False.
    """
    default = False
    callback_event = wx.EVT_CHECKBOX

    # For i18n support, load your i18n conversion stuff and change
    # these class attributes
    yes_values = ["yes", "true", "1"]
    no_values = ["no", "false", "0"]
    
    def getCtrl(self, parent, initial=None):
        """Return a checkbox control"""
        ctrl = wx.CheckBox(parent, -1, "")
        return ctrl

    def textToValue(self, text):
        """Return True if one of the yes values, otherwise False"""
        text = Param.textToValue(self, text).lower()
        if text in self.yes_values:
            return True
        return False

    def valueToText(self, value):
        """Convert the boolean value to a string"""
        if value:
            return self.yes_values[0]
        return self.no_values[0]

    def setValue(self, ctrl, value):
        # the control operates on boolean values directly, so no
        # conversion is needed.
        ctrl.SetValue(value)

    def getValue(self, ctrl):
        # The control returns booleans, which is what we want
        return ctrl.GetValue()

class IntParam(Param):
    """Int parameter that displays a text entry field as its user interface.

    The text is converted through the locale atof conversion, then
    cast to an integer.
    """
    default = 0
    
    def textToValue(self, text):
        text = Param.textToValue(self, text)
        tmp = locale.atof(text)
        val = int(tmp)
        return val

    def setValue(self, ctrl, value):
        ctrl.SetValue(str(value))

    def getValue(self, ctrl):
        return int(ctrl.GetValue())

class FloatParam(Param):
    """Int parameter that displays a text entry field as its user interface.

    The text is converted through the locale atof conversion.
    """
    default = 0.0
    
    def textToValue(self, text):
        text = Param.textToValue(self, text)
        val = locale.atof(text)
        return val

    def setValue(self, ctrl, value):
        ctrl.SetValue(str(value))

    def getValue(self, ctrl):
        return float(ctrl.GetValue())

class StrParam(Param):
    """String parameter that displays a text entry field as its user interface.

    This is an alias to the Param class.
    """
    default = ""
    pass

class UnquotedStrParam(StrParam):
    """String parameter that doesn't enclose the string in quotes"""
    def valueToText(self, value):
        return value


class CommaSeparatedListParam(StrParam):
    """Parameter that is a list of comma separated strings.

    The user interface presents a simple text box that takes a comma separated
    list of strings.  Unlike the L{CommaSeparatedStringSetParam} which sorts
    the values, this param keeps the strings in the order entered by the user.
    
    The data value is a python list(), so the classpref's value can be adjusted
    using the standard list methods.
    """
    def isIterableDataType(self):
        return True
    
    def valueToText(self, value):
        text = ", ".join([str(v) for v in value])
        return text
    
    def getListValues(self, text):
        text = text.strip()
        if ',' in text:
            raw = text.split(',')
        else:
            raw = [text]
        values = []
        for v in raw:
            values.append(v.strip())
        return values
    
    def textToValue(self, text):
        return self.getListValues(text)

    def setValue(self, ctrl, value):
        text = self.valueToText(value)
        ctrl.SetValue(text)

    def getValue(self, ctrl):
        text = ctrl.GetValue()
        return self.getListValues(text)

class CommaSeparatedStringSetParam(CommaSeparatedListParam):
    """Parameter that is a set of comma separated strings.

    The user interface presents a simple text box that takes a comma separated
    list of strings.  The list of strings is unordered, and will appear
    lexicographically sorted in the config file.
    
    The data value is a python set(), so the classpref's value can be adjusted
    using the standard set methods.
    """
    def valueToText(self, value):
        values = [str(v) for v in value]
        values.sort()
        text = ", ".join(values)
        return text
    
    def getListValues(self, text):
        text = text.strip()
        if ',' in text:
            raw = text.split(',')
        else:
            raw = [text]
        values = set()
        for v in raw:
            values.add(v.strip())
        return values


class DateParam(Param):
    """Date parameter that displays a DatePickerCtrl as its user
    interface.

    The wx.DateTime class is used as the value.
    """
    default = wx.DateTime().Today()
    callback_event = wx.EVT_DATE_CHANGED
    
    def getCtrl(self, parent, initial=None):
        dpc = wx.DatePickerCtrl(parent, size=(120,-1),
                                style=wx.DP_DROPDOWN | wx.DP_SHOWCENTURY)
        return dpc

    def textToValue(self, text):
        date_pattern = "(\d+)\D+(\d+)\D+(\d+)"
        match = re.search(date_pattern, text)
        if match:
            assert self.dprint("ymd = %d/%d/%d" % (int(match.group(1)),
                                                   int(match.group(2)),
                                                   int(match.group(3))))
            t = time.mktime([int(match.group(1)), int(match.group(2)),
                             int(match.group(3)), 12, 0, 0, 0, 0, 0])
            dt = wx.DateTimeFromTimeT(t)
        else:
            dt = wx.DateTime()
            dt.Today()
        assert self.dprint(dt)
        return dt

    def valueToText(self, dt):
        assert self.dprint(dt)
        text = "{ %d , %d , %d }" % (dt.GetYear(), dt.GetMonth() + 1,
                                     dt.GetDay())
        return text

    def getValue(self, ctrl):
        return ctrl.GetValue()

class DirParam(Param):
    """Directory parameter that displays a DirBrowseButton2 as its
    user interface.

    A string that represents a directory path is used as the value.
    It will always be a normalized path.
    """
    default = os.path.dirname(os.getcwd())
    callback_event = None

    def getCtrl(self, parent, initial=None):
        if initial is None:
            initial = os.getcwd()
        c = DirBrowseButton2(parent, -1, size=(300, -1),
                             labelText = '', startDirectory=initial)
        return c

    def setValue(self, ctrl, value):
        if value:
            # only try to use normpath when the value exists.  Allow a value of
            # "" to be displayed in the text control.
            value = os.path.normpath(value)
        ctrl.SetValue(value)

    def OnCallback(self, evt):
        """Specific implementation for DirParam.
        
        The normal OnCallback method of the parent class can't be used because
        the change event for the DirBrowseButton/FileBrowseButton composite
        controls actually occurs on the text control, which is the immediate
        child of the browsebutton control.
        """
        # Get the browse button control, which is the parent of the text
        # control that the event occurs on
        ctrl = evt.GetEventObject().GetParent()
        if self.user_initiated_callback:
            self.processCallback(evt, ctrl, ctrl.ctrl_list)
            if self.processCallbackHook:
                self.processCallbackHook(self, evt, ctrl, ctrl.ctrl_list)
    
    def setCallback(self, ctrl, ctrl_list):
        """Set the callback that responds to changes in the state of the control
        
        Because the DirBrowseButton/FileBrowseButton have their own built-in
        way of specifying a callback, we have to override L{Param.setCallback}
        with this function that sets the callback as required by the browse
        button control.
        """
        ctrl.ctrl_list = ctrl_list
        ctrl.changeCallback = self.OnCallback
    
    def setValueWithoutCallback(self, ctrl, value):
        # use ChangeValue which explicitly doesn't fire the change callback.
        # The user_initiated_callback trick doesn't work with the
        # FileBrowseButton
        ctrl.textControl.ChangeValue(value)

class PathParam(DirParam):
    """Directory parameter that displays a FileBrowseButton as its
    user interface.

    A string that represents a path to a file is used as the value.
    """
    default = os.getcwd()
    
    def getCtrl(self, parent, initial=None):
        if initial is None:
            initial = os.getcwd()
        c = FileBrowseButton2(parent, -1, size=(300, -1),
                                        labelText = '',
                                        startDirectory=initial)
        return c


class ChoiceParam(Param):
    """Parameter that is restricted to a string from a list of choices.

    A Choice control is its user interface, and the currently selected
    string is used as the value.
    """
    callback_event = wx.EVT_CHOICE
    
    def __init__(self, keyword, choices, default=None, help='', **kwargs):
        Param.__init__(self, keyword, help=help, **kwargs)
        self.choices = choices
        if default is not None:
            self.default = default
        else:
            self.default = choices[0]

    def getCtrl(self, parent, initial=None):
        ctrl = wx.Choice(parent , -1, (100, 50), choices = self.choices)
        return ctrl

    def setValue(self, ctrl, value):
        #dprint("%s in %s" % (value, self.choices))
        try:
            index = self.choices.index(value)
        except ValueError:
            index = 0
        ctrl.SetSelection(index)

    def getValue(self, ctrl):
        index = ctrl.GetSelection()
        return self.choices[index]

class IndexChoiceParam(Param):
    """Parameter that is restricted to a string from a list of
    choices, but using an integer as the value.

    The user interface presents a list of strings, but the value is either
    the index of the string within the list in the simple case, or using a map
    of index to another integer.  If the choices are a simple text string,
    index mode is used; however, if the choices are each tuples of an int
    and a string, the int in the tuple is used as the value.
    
    For example, IndexChoiceParam('Citrus', ['Lemon','Lime','Grapefruit']) is
    a simple index lookup, from the set {0, 1, 2}, where the following uses a
    keyed lookup and represents the value as the integer in the first element
    of each tuple:
        
      IndexChoiceParam('coins', [(1, 'penny'), (5, 'nickel'), (10, 'dime')])
    """
    callback_event = wx.EVT_CHOICE
    
    def __init__(self, keyword, choices, default=None, help='', **kwargs):
        Param.__init__(self, keyword, help=help, **kwargs)
        if isinstance(choices[0], list) or isinstance(choices[0], tuple):
            self.choices = []
            self.index_to_value = {}
            self.value_to_index = {}
            index = 0
            for c in choices:
                self.index_to_value[index] = c[0]
                self.value_to_index[c[0]] = index
                self.choices.append(c[1])
                index += 1
        else:
            self.choices = choices
            self.index_to_value = self.value_to_index = range(0, len(choices))
        if default is not None and default in self.value_to_index:
            self.default = default
        else:
            self.default = self.index_to_value[0]

    def getCtrl(self, parent, initial=None):
        ctrl = wx.Choice(parent , -1, (100, 50), choices = self.choices)
        ctrl.SetSelection(self.value_to_index[self.default])
        return ctrl

    def convertStringValueToCorrectType(self, value):
        tmp = locale.atof(value)
        value = int(tmp)
        if value not in self.value_to_index:
            value = self.default
        return value

    def textToValue(self, text):
        try:
            value = Param.textToValue(self, text)
            val = self.convertStringValueToCorrectType(value)
        except:
            val = self.default
        return val

    def setValue(self, ctrl, value):
        index = self.value_to_index[value]
        if index >= len(self.choices):
            index = 0
        ctrl.SetSelection(index)

    def getValue(self, ctrl):
        index = ctrl.GetSelection()
        return self.index_to_value[index]

class StringIndexChoiceParam(IndexChoiceParam):
    """Same as IndexChoiceParem, but using strings as the keys instead of
    integers.
    
    """
    def convertStringValueToCorrectType(self, value):
        if value not in self.value_to_index:
            value = self.default
        return value

class KeyedIndexChoiceParam(IndexChoiceParam):
    """Parameter that is restricted to a string from a list of
    choices, but using a key corresponding to the string as the value.

    A Choice control is its user interface, and the key of the
    currently selected string is used as the value.

    An example:
    
        KeyedIndexChoiceParam('edge_indicator',
                              [(wx.stc.STC_EDGE_NONE, 'none'),
                               (wx.stc.STC_EDGE_LINE, 'line'),
                               (wx.stc.STC_EDGE_BACKGROUND, 'background'),
                               ], 'line', help='Long line indication mode'),

    means that the user code deals with wx.stc.STC_EDGE_NONE,
    wx.stc.STC_EDGE_LINE, and wx.stc.STC_EDGE_BACKGROUND, but stored
    in the configuration will be 'none', 'line', or 'background'.  The
    strings 'none', 'line', or 'background' are the items that will be
    displayed in the choice control.
    """
    def __init__(self, keyword, choices, default=None, help='', **kwargs):
        Param.__init__(self, keyword, help=help, **kwargs)
        self.choices = [entry[1] for entry in choices]
        self.keys = [entry[0] for entry in choices]
        if default is not None and default in self.choices:
            self.default = self.keys[self.choices.index(default)]
        else:
            self.default = self.keys[0]

    def getCtrl(self, parent, initial=None):
        ctrl = wx.Choice(parent , -1, (100, 50), choices = self.choices)
        index = self.keys.index(self.default)
        ctrl.SetSelection(index)
        return ctrl

    def textToValue(self, text):
        text = Param.textToValue(self, text)
        try:
            index = self.choices.index(text)
        except ValueError:
            index = 0
        return self.keys[index]

    def valueToText(self, value):
        index = self.keys.index(value)
        return str(self.choices[index])

    def setValue(self, ctrl, value):
        index = self.keys.index(value)
        ctrl.SetSelection(index)

    def getValue(self, ctrl):
        index = ctrl.GetSelection()
        return self.keys[index]


myEVT_EDITABLE_CHOICE = wx.NewEventType()
EVT_EDITABLE_CHOICE = wx.PyEventBinder(myEVT_EDITABLE_CHOICE, 1)

class EditableChoiceEvent(wx.PyCommandEvent):
    """Combination event used with L{EditableChoice} to indicate the combobox
    has either changed selection or been renamed.
    
    """
    def __init__(self, obj, selected=-1, text=None, action=None):
        wx.PyCommandEvent.__init__(self, myEVT_EDITABLE_CHOICE, id=-1)
        self.selected = selected
        self.text = text
        self.action = action
        self.SetEventObject(obj)
    
    def GetSelection(self):
        return self.selected
    
    def IsRenamed(self):
        return self.action == 'rename'
    
    def IsNew(self):
        return self.action == 'new'
    
    def IsDelete(self):
        return self.action == 'delete'
    
    def GetValue(self):
        return self.text
    
class EditableChoice(wx.Panel):
    """Multi-control panel including a combobox to select or rename items,
    and buttons to add or delete items.
    
    Used as the user interface element of the L{UserListParamStart} param.
    """
    def __init__(self, parent, id, *args, **kwargs):
        choices = kwargs['choices']
        del kwargs['choices']
        wx.Panel.__init__(self, parent, id, *args, **kwargs)
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.SetSizer(sizer)
        self.choice = wx.ComboBox(self , -1, choices=choices)
        sizer.Add(self.choice, 1, wx.EXPAND)
        self.add = wx.Button(self, -1, _("New Item"))
        sizer.Add(self.add, 0, wx.EXPAND)
        self.delete = wx.Button(self, -1, _("Delete"))
        sizer.Add(self.delete, 0, wx.EXPAND)
        
        self.choice.Bind(wx.EVT_COMBOBOX, self.OnChoice)
        self.choice.Bind(wx.EVT_TEXT, self.OnText)
        self.add.Bind(wx.EVT_BUTTON, self.OnNew)
        self.delete.Bind(wx.EVT_BUTTON, self.OnDelete)
        self.delete.Enable(self.choice.GetCount() > 1)
    
    def OnChoice(self, evt):
        e = EditableChoiceEvent(self, evt.GetSelection())
        wx.CallAfter(self.launcheEvent, e)
    
    def OnText(self, evt):
        #dprint(self.choice.GetSelection())
        e = EditableChoiceEvent(self, self.choice.GetSelection(), evt.GetString(), action='rename')
        wx.CallAfter(self.launcheEvent, e)
    
    def OnNew(self, evt):
        text = self.findNewItem()
        #dprint("adding new item %s" % text)
        e = EditableChoiceEvent(self, -1, text, action='new')
        wx.CallAfter(self.launcheEvent, e)
    
    def OnDelete(self, evt):
        text = self.choice.GetValue()
        #dprint("Deleting item %s" % text)
        e = EditableChoiceEvent(self, -1, text, action='delete')
        wx.CallAfter(self.launcheEvent, e)
    
    def launcheEvent(self, evt):
        self.GetEventHandler().ProcessEvent(evt)
    
    def findNewItem(self, name="New Item"):
        """Find a name for a new item that is guaranteed not to appear in the
        current list.
        """
        text = _(name)
        count = 0
        while self.choice.FindString(text) >= 0:
            count += 1
            text = "%s<%d>" % (_(name), count)
        return text
    
    def SetSelection(self, index):
        self.choice.SetSelection(index)
    
    def GetSelection(self):
        return self.choice.GetSelection()
    
    def Clear(self):
        self.choice.Clear()
        self.delete.Enable(False)
    
    def Append(self, entry):
        self.choice.Append(entry)
        self.delete.Enable(self.choice.GetCount() > 1)
    
    def Delete(self, entry):
        self.choice.Delete(entry)
        self.delete.Enable(self.choice.GetCount() > 1)
    
    def SetString(self, index, text):
        self.choice.SetString(index, text)


class UserListParamStart(Param):
    """User customizable list that controls several other parameters and also
    allows the user to enter new versions of the parameters.

    A Choice control is its user interface, but changing that control changes
    the other dependent controls.
    """
    callback_event = EVT_EDITABLE_CHOICE
    
    class ListState(object):
        def __init__(self, name):
            self.name = name
            self.dependent_values = {}
        
        @classmethod
        def getArray(cls, choices):
            return [cls(c) for c in choices]
    
    def __init__(self, keyword, dependent_keywords, title='', help='', **kwargs):
        if 'fullwidth' not in kwargs:
            kwargs['fullwidth'] = True
        Param.__init__(self, keyword, help=help, **kwargs)
        self.dependent_keywords = dependent_keywords
        self.choices = [UserListParamStart.ListState("default")]
        self.orig_keyword_subs = []
        self.current_selection = 0
        self.dependent_params = []
        self.title = title
    
    def getStaticBoxTitle(self):
        return self.title

    def isDependentKeywordStart(self):
        return True
    
    def setInitialDefaults(self, ctrl, ctrls, default_getter, orig):
        if default_getter is not None:
            for keyword in self.dependent_keywords:
                dependent_param = self.findParamFromKeyword(keyword, ctrls)
                dependent_param.setInitialDefaults(ctrls[dependent_param], ctrls, default_getter, orig)
            
            self.current_selection = 0
            try:
                # Find the subscripts using one of dependent keyword as the
                # example
                choices, default = default_getter._getAllSubscripts(self.dependent_keywords[0], self.keyword)
                #dprint("%s; default=%d" % (choices, default))
                self.current_selection = default
            except AttributeError:
                val = default_getter(self.keyword)
                #dprint("keyword %s: val = %s(%s)" % (self.keyword, val, type(val)))
                choices = val.split(',')
            if choices:
                ctrl.Clear()
                for choice in choices:
                    ctrl.Append(choice)
                self.choices = UserListParamStart.ListState.getArray(choices)
                self.setValue(ctrl, self.current_selection)
                orig[self.keyword] = choices[self.current_selection]
            else:
                orig[self.keyword] = None
            
            self.saveOriginalParams(ctrls, orig, default_getter)
            
            # Initialize the dependent keywords.  NOTE: this depends on the
            # initial default being delayed until all the dependent controls
            # have been instantiated.
            self.changeDependentKeywords(ctrls)

    def getCtrl(self, parent, initial=None):
        ctrl = EditableChoice(parent , -1, choices = [c.name for c in self.choices])
        return ctrl

    def setValue(self, ctrl, index):
        #dprint("%s in %s" % (value, self.choices))
        ctrl.SetSelection(index)

    def getValue(self, ctrl):
        index = ctrl.GetSelection()
        return index
    
    def processCallback(self, evt, ctrl, ctrl_list):
        new_selection = evt.GetSelection()
        if new_selection == -1:
            choice = evt.GetValue()
            if evt.IsRenamed():
                index = self.current_selection
                current = self.choices[index].name
                #dprint("New name for index %d: %s: %s" % (index, current, choice))
                self.choices[index].name = choice
                ctrl.SetString(index, choice)
            elif evt.IsNew():
                ctrl.Append(choice)
                self.choices.append(UserListParamStart.ListState(choice))
                self.current_selection = len(self.choices) - 1
                state = self.choices[self.current_selection]
                ctrl.SetSelection(self.current_selection)
                for keyword in self.dependent_keywords:
                    dependent_param = self.findParamFromKeyword(keyword, ctrl_list)
                    state.dependent_values[keyword] = dependent_param.default
                self.changeDependentKeywords(ctrl_list)
            elif evt.IsDelete():
                ctrl.Delete(self.current_selection)
                self.choices[self.current_selection:self.current_selection+1] = []
                self.current_selection = min(self.current_selection, len(self.choices) - 1)
                ctrl.SetSelection(self.current_selection)
                choice = self.choices[self.current_selection]
                self.changeDependentKeywords(ctrl_list)
        elif new_selection != self.current_selection:
            choice = self.choices[new_selection]
            #dprint("old=%s new=%s new choice=%s" % (self.current_selection, new_selection, choice.name))
            self.saveDependentKeywords(ctrl_list)
            self.current_selection = new_selection
            self.changeDependentKeywords(ctrl_list)
    
    def saveOriginalParams(self, ctrls, orig, default_getter):
        """Save all the original dependent info so it can be compared with any
        user edits later.
        
        """
        for keyword in self.dependent_keywords:
            dependent_param = self.findParamFromKeyword(keyword, ctrls)
            for choice in self.choices:
                keyword_sub = "%s[%s]" % (keyword, choice.name)
                try:
                    val = default_getter(keyword_sub)
                    #dprint("orig[%s] = %s" % (keyword_sub, val))
                    orig[keyword_sub] = val
                except (AttributeError, KeyError):
                    val = default_getter(keyword)
                    self.dprint("using default value for orig[%s]: orig[%s] = %s" % (keyword_sub, keyword, val))
                    # Force the default values to be written to the preferences
                    # file by flagging their original values as None
                    orig[keyword_sub] = None
                    
                self.orig_keyword_subs.append(keyword_sub)
                choice.dependent_values[keyword] = val
    
    def saveDependentKeywords(self, ctrls):
        """Change the values of the dependent controls when a new item is
        selected.
        
        """
        selection = self.current_selection
        choice = self.choices[selection]
        for keyword in self.dependent_keywords:
            dependent_param = self.findParamFromKeyword(keyword, ctrls)
            ctrl = ctrls[dependent_param]
            val = dependent_param.getValue(ctrl)
            #dprint("keyword %s[%s]: val = %s(%s)" % (keyword, choice.name, val, type(val)))
            choice.dependent_values[keyword] = val
    
    def changeDependentKeywords(self, ctrls):
        """Change the values of the dependent controls when a new item is
        selected.
        
        """
        selection = self.current_selection
        choice = self.choices[selection]
        for keyword in self.dependent_keywords:
            dependent_param = self.findParamFromKeyword(keyword, ctrls)
            val = choice.dependent_values[keyword]
            #dprint("keyword %s[%s]: val = %s(%s)" % (keyword, choice.name, val, type(val)))
            ctrl = ctrls[dependent_param]
            dependent_param.setValue(ctrl, val)

    def updateIfModified(self, ctrls, orig, setter, locals=None):
        """Update values if the user has changed any items
        
        @param ctrls: dict of ctrls keyed on param
        
        @param orig: dict keyed on param object that contains the original
        value of the item.
        
        @param setter: functor that takes two arguments: the keyword and the
        value to set the value in the user's classpref.
        """
        # The user may have made changes to the dependent keywords, but
        # ordinarily changes are only saved when the choice control is
        # modified.  We need to force this save so the final changes the user
        # may have made before pressing the OK button on the dialog are saved.
        self.saveDependentKeywords(ctrls)
        
        # check to see if any value of any subscript has changed.  If even a
        # single entry in a subscript has changed, save all the data for the
        # subscript.
        changed = {}
        for choice in self.choices:
            changed[choice] = False
            for keyword in self.dependent_keywords:
                keyword_sub = "%s[%s]" % (keyword, choice.name)
                val = choice.dependent_values[keyword]
                if keyword_sub not in orig or val != orig[keyword_sub]:
                    changed[choice] = True
                    break
        #dprint(changed)
        
        # Now make the changes to the dependent keywords
        valid_keyword_subs = {}
        for choice in self.choices:
            for keyword in self.dependent_keywords:
                keyword_sub = "%s[%s]" % (keyword, choice.name)
                valid_keyword_subs[keyword_sub] = True
                if not changed[choice]:
                    continue
                val = choice.dependent_values[keyword]
                self.dprint("%s has changed from %s(%s) to %s(%s)" % (keyword_sub, orig.get(keyword_sub, None), type(orig.get(keyword_sub, None)), val, type(val)))
                setter(keyword_sub, val)
                if locals and param.local:
                    locals[keyword_sub] = val
        
        # Remove any deleted items
        for keyword_sub in self.orig_keyword_subs:
            if keyword_sub not in valid_keyword_subs:
                #dprint("Deleting %s" % keyword_sub)
                setter(keyword_sub, None)
        
        # and finally make the changes to the list keyword
        val = self.choices[self.current_selection].name
        if val != orig[self.keyword]:
            self.dprint("%s has changed from %s(%s) to %s(%s)" % (self.keyword, orig[self.keyword], type(orig[self.keyword]), val, type(val)))
            setter(self.keyword, val)
            if locals and param.local:
                locals[self.keyword] = val


class UserListParamEnd(BoolParam):
    """User customizable list that controls several other parameters and also
    allows the user to enter new versions of the parameters.

    A Choice control is its user interface, but changing that control changes
    the other dependent controls.
    """
    callback_event = wx.EVT_CHOICE
    
    def __init__(self, keyword, help='', **kwargs):
        self.start_keyword = keyword
        keyword += "_end_"
        Param.__init__(self, keyword, help=help, **kwargs)

    def isDependentKeywordEnd(self):
        return True
    
    def updateIfModified(self, ctrls, orig, setter, locals=None):
        pass


class FontParam(Param):
    """Font parameter that pops up a font dialog.

    The wx.Font class is used as the value.
    """
    callback_event = None
    
    def getCtrl(self, parent, initial=None):
        btn = FontBrowseButton(parent)
        return btn

    def textToValue(self, text):
        face, size = text.split(',')
        face = face.strip()
        size = int(size.strip())
        font = wx.FFont(size, wx.DEFAULT, face=face)
        return font

    def valueToText(self, font):
        assert self.dprint(font)
        text = "%s, %d" % (font.GetFaceName(), font.GetPointSize())
        return text

    def setValue(self, ctrl, value):
        ctrl.setFont(value)

    def getValue(self, ctrl):
        return ctrl.getFont()



parentclasses={}
skipclasses=['debugmixin','ClassPrefs','object']

def getAllSubclassesOf(parent=debugmixin, subclassof=None):
    """
    Recursive call to get all classes that have a specified class
    in their ancestry.  The call to __subclasses__ only finds the
    direct, child subclasses of an object, so to find
    grandchildren and objects further down the tree, we have to go
    recursively down each subclasses hierarchy to see if the
    subclasses are of the type we want.

    @param parent: class used to find subclasses
    @type parent: class
    @param subclassof: class used to verify type during recursive calls
    @type subclassof: class
    @returns: list of classes
    """
    if subclassof is None:
        subclassof=parent
    subclasses={}

    # this call only returns immediate (child) subclasses, not
    # grandchild subclasses where there is an intermediate class
    # between the two.
    classes=parent.__subclasses__()
    for kls in classes:
        # FIXME: this seems to return multiple copies of the same class,
        # but I probably just don't understand enough about how python
        # creates subclasses
        # dprint("%s id=%s" % (kls, id(kls)))
        if issubclass(kls,subclassof):
            subclasses[kls] = 1
        # for each subclass, recurse through its subclasses to
        # make sure we're not missing any descendants.
        subs=getAllSubclassesOf(parent=kls)
        if len(subs)>0:
            for kls in subs:
                subclasses[kls] = 1
    return subclasses.keys()

def parents(c, seen=None):
    """Python class base-finder.

    Find the list of parent classes of the given class.  Works with
    either old style or new style classes.  From
    http://mail.python.org/pipermail/python-list/2002-November/132750.html
    """
    if type( c ) == types.ClassType:
        # It's an old-style class that doesn't descend from
        # object.  Make a list of parents ourselves.
        if seen is None:
            seen = {}
        seen[c] = None
        items = [c]
        for base in c.__bases__:
            if not seen.has_key(base):
                items.extend( parents(base, seen))
        return items
    else:
        # It's a new-style class.  Easy.
        return list(c.__mro__)

def getClassHierarchy(klass,debug=0):
    """Get class hierarchy of a class using global class cache.

    If the class has already been seen, it will be pulled from the
    cache and the results will be immediately returned.  If not, the
    hierarchy is generated, stored for future reference, and returned.

    @param klass: class of interest
    @returns: list of parent classes
    """
    global parentclasses
    
    if klass in parentclasses:
        hierarchy=parentclasses[klass]
        if debug: dprint("Found class hierarchy: %s" % hierarchy)
    else:
        hierarchy=[k for k in parents(klass) if k.__name__ not in skipclasses and not k.__module__.startswith('wx.')]
        if debug: dprint("Created class hierarchy: %s" % hierarchy)
        parentclasses[klass]=hierarchy
    return hierarchy

def getHierarchy(obj,debug=0):
    """Get class hierarchy of an object using global class cache.

    If the class has already been seen, it will be pulled from the
    cache and the results will be immediately returned.  If not, the
    hierarchy is generated, stored for future reference, and returned.

    @param obj: object of interest
    @returns: list of parent classes
    """
    klass=obj.__class__
    return getClassHierarchy(klass,debug)

def getNameHierarchy(obj,debug=0):
    """Get the class name hierarchy.

    Given an object, return a list of names of parent classes.
    
    Similar to L{getHierarchy}, except returns the text names of the
    classes instead of the class objects themselves.

    @param obj: object of interest
    @returns: list of strings naming each parent class
    """
    
    hierarchy=getHierarchy(obj,debug)
    names=[k.__name__ for k in hierarchy]
    if debug:  dprint("name hierarchy: %s" % names)
    return names

def getSubclassHierarchy(obj,subclass,debug=0):
    """Return class hierarchy of a particular subclass.

    Given an object, return the hierarchy of classes that are
    subclasses of a given type.  In other words, this filters the
    output of C{getHierarchy} such that only the classes that are
    descended from the desired subclass are returned.

    @param obj: object of interest
    @param subclass: subclass type on which to filter
    @returns: list of strings naming each parent class
    """
    
    klasses=getHierarchy(obj)
    subclasses=[]
    for klass in klasses:
        if issubclass(klass,subclass):
            subclasses.append(klass)
    return subclasses
    

class GlobalPrefs(debugmixin):
    debuglevel = 0
    
    default={}
    params = {}
    seen = {} # has the default_classprefs been seen for the class?
    convert_already_seen = {}
    
    # configuration has been loaded from text for this class, but not
    # converted yet.
    needs_conversion = {}
    
    user={}
    name_hierarchy={}
    class_hierarchy={}
    magic_conversion=True
    
    @classmethod
    def setDefaults(cls, defs):
        """Set system defaults to the dict of dicts.

        The GlobalPrefs.default values are used as failsafe defaults
        -- they are only used if the user defaults aren't found.

        The outer layer dict is class name, the inner dict has
        name/value pairs.  Note that the value's type should match
        with the default_classprefs, or there'll be trouble.
        """
        d = cls.default
        p = cls.params
        for section, defaults in defs.iteritems():
            if section not in d:
                d[section] = {}
            d[section].update(defaults)
            if section not in p:
                p[section] = {}

    @classmethod
    def addHierarchy(cls, leaf, classhier, namehier):
        #dprint(namehier)
        if leaf not in cls.class_hierarchy:
            cls.class_hierarchy[leaf]=classhier
        if leaf not in cls.name_hierarchy:
            cls.name_hierarchy[leaf]=namehier

    @classmethod
    def setupHierarchyDefaults(cls, klasshier):
        helptext = {}
        for klass in klasshier:
#            if klass.__name__ == "CommonlyUsedMajorModes":
#                cls.debuglevel = 1
#            else:
#                cls.debuglevel = 0
            if klass.__name__ not in cls.user:
                cls.user[klass.__name__]={}
            
            if klass.__name__ not in cls.default:
                #dprint("%s not seen before" % klass)
                defs={}
                params = {}
                if hasattr(klass,'default_classprefs'):
                    cls.seen[klass.__name__] = True
                    for p in klass.default_classprefs:
                        if not p.isSuperseded():
                            defs[p.keyword] = p.default
                            params[p.keyword] = p
                        if p.isOverridingSuperclass():
                            # If the definition in the class should take
                            # precidence over any superclasses params, force
                            # the user value so that the searching will
                            # stop with this value and won't proceed up the
                            # hierarchy.
                            if p.keyword not in cls.user[klass.__name__]:
                                value = p.valueToText(p.default)
                                cls.user[klass.__name__][p.keyword] = value
                                #dprint("Overriding %s.%s with %s" % (klass.__name__, p.keyword, value))
                                cls.needs_conversion[klass.__name__] = True
                        if p.help:
                            helptext[p.keyword] = p.help
                        if p.isIterableDataType():
                            # For lists, sets, and hashes, the value of the
                            # contents of the classpref can change without
                            # updating the classpref itself.  It's the
                            # different between appending to a list where
                            # the list pointer doesn't change to assigning
                            # a new value to the classpref which updates the
                            # GlobalPrefs.user dict.  So, we force the update
                            # here so that the value is always checked when
                            # writing the config file.
                            if p.keyword not in cls.user[klass.__name__]:
                                cls.user[klass.__name__][p.keyword] = p.valueToText(p.default)
                                if cls.debuglevel > 0: dprint("no value from config file; setting default for iterable data type %s[%s] = %s" % (klass.__name__, p.keyword, cls.user[klass.__name__][p.keyword]))
                            cls.needs_conversion[klass.__name__] = True
                cls.default[klass.__name__]=defs
                cls.params[klass.__name__] = params
            else:
                #dprint("%s app defaults seen" % klass)
                # we've loaded application-specified defaults for this
                # class before, but haven't actually checked the
                # class.  Merge them in without overwriting the
                # existing prefs.
                #print "!!!!! missed %s" % klass
                if hasattr(klass,'default_classprefs'):
                    cls.seen[klass.__name__] = True
                    gd = cls.default[klass.__name__]
                    gp = cls.params[klass.__name__]
                    for p in klass.default_classprefs:
                        if not p.isSuperseded():
                            if p.keyword not in gd:
                                gd[p.keyword] = p.default
                            if p.keyword not in gp:
                                gp[p.keyword] = p
                        if p.help:
                            helptext[p.keyword] = p.help
        if cls.debuglevel > 1: dprint("default: %s" % cls.default)
        if cls.debuglevel > 1: dprint("user: %s" % cls.user)
        
        cls.setMissingParamData(klasshier, helptext)
    
    @classmethod
    def setMissingParamData(cls, klasshier, helptext):
        """Update any missing param data using info from superclasses
        
        If a L{Param} has missing help text, look up the class hierarchy for
        the help text of the param that it's shadowing and use its help text
        """
        r = klasshier[:]
        r.reverse()
        for klass in r:
            if hasattr(klass,'default_classprefs'):
                for p in klass.default_classprefs:
                    if not p.help and p.keyword in helptext:
                        p.help = helptext[p.keyword]

    @classmethod
    def findParam(cls, section, option):
        """Search up the class hierarchy for a parameter matching a
        configuration option.
        
        @param section: Section name of the config file; must match the class
        name of a class.
        
        @param option: option name in the config file; same as the keyword of a
        L{Param} instance as defined in the default_classprefs class attribute.
        """
        params = cls.params
        param = None
        
        # Check for the presence of UserList options.  These options will be in
        # the form of an array, like:
        #
        # option1[index1] = value1
        # option1[index2] = value2
        index = option.find("[")
        if index > 0:
            option = option[:index]
            
        if section in params and option in params[section]:
            param = params[section][option]
        elif section in cls.name_hierarchy:
            # Need to march up the class hierarchy to find the correct
            # Param
            klasses=cls.name_hierarchy[section]
            #dprint(klasses)
            for name in klasses[1:]:
                if name in params and option in params[name]:
                    param = params[name][option]
                    break
        if param is None:
            if cls.debuglevel > 0: dprint("Unknown configuration for %s: %s" % (section, option))
            return None
        if cls.debuglevel > 0: dprint("Found %s for %s in class %s" % (param.__class__.__name__, option, section))
        return param


    @classmethod
    def findUserListParamStartOf(cls, section, option):
        """Search up the class hierarchy for the L{UserListParamStart}
        parameter of the specified
        
        @param section: Section name of the config file; must match the class
        name of a class.
        
        @param option: option name in the config file; same as the keyword of a
        L{Param} instance as defined in the default_classprefs class attribute.
        """
        params = cls.params
        
        # Check for the presence of UserList options.  These options will be in
        # the form of an array, like:
        #
        # option1[index1] = value1
        # option1[index2] = value2
        index = option.find("[")
        if index > 0:
            option = option[:index]
        if cls.debuglevel > 0: dprint("Searching for %s" % option)
        
        start = None
        # Need to march up the class hierarchy to find the correct
        # Param
        klasses=cls.class_hierarchy[section]
        #dprint(klasses)
        for klass in klasses:
            if hasattr(klass, 'default_classprefs'):
                for p in klass.default_classprefs:
                    if cls.debuglevel > 0: dprint(p)
                    if p.isDependentKeywordStart():
                        start = p
                    elif p.keyword == option:
                        if cls.debuglevel > 0: dprint("Found %s for %s" % (start, option))
                        return start

    @classmethod
    def readConfig(cls, fh):
        """Load a configuration file.
        
        This method loads an INI-style config file and places the unconverted
        text into a dict based on the section name.
        """
        cfg=ConfigParser()
        cfg.optionxform=str
        cfg.readfp(fh)
        
        # Force conversion of all sections
        cls.convert_already_seen = {}
        
        for section in cfg.sections():
#            if section == "UserList":
#                cls.debuglevel = 1
#            else:
#                cls.debuglevel = 0
            cls.needs_conversion[section] = True
            d={}
            for option, text in cfg.items(section):
                # NOTE! text will be converted later, after all
                # plugins are loaded and we know what type each
                # parameter is supposed to be
                d[option]=text
            if cls.debuglevel > 0: dprint(d)
            if section in cls.user:
                cls.user[section].update(d)
            else:
                cls.user[section]=d
    
    @classmethod
    def convertSection(cls, section):
#        if section == "UserList":
#            cls.debuglevel = 1
#        else:
#            cls.debuglevel = 0
        if section not in cls.params or section in cls.convert_already_seen or section not in cls.seen:
            # Don't process values before the param definition for
            # the class is loaded.  Copy the existing text values
            # and defer the conversion till the next time
            # convertConfig is called.
            if cls.debuglevel > 0:
                if section not in cls.params:
                    dprint("haven't loaded class %s" % section)
                elif section in cls.convert_already_seen:
                    dprint("already converted class %s" % section)
                elif section not in cls.seen:
                    dprint("only defaults loaded, haven't loaded Params for class %s" % section)
            return
        
        cls.convert_already_seen[section] = True
        if section in cls.needs_conversion:
            options = cls.user[section]
            d = {}
            
            # Keep track of any userlist params so the dependent parameters can
            # be updated
            userlist_params = set()
            found_userlist_entries = False
            for option, text in options.iteritems():
                param = cls.findParam(section, option)
                try:
                    if param is not None:
                        val = param.textToValue(text)
                        if cls.debuglevel > 0: dprint("Converted %s to %s(%s) for %s[%s]" % (text, val, type(val), section, option))
                        if param.isDependentKeywordStart():
                            userlist_params.add(param)
                    else:
                        if cls.debuglevel > 0: dprint("Didn't find param for %s" % option)
                        val = text
                    d[option] = val
                    if not found_userlist_entries:
                        index = option.find("[")
                        if index > 0:
                            found_userlist_entries = True
                except Exception, e:
                    eprint("Error converting %s in section %s: %s" % (option, section, str(e)))
            
            if found_userlist_entries and not userlist_params:
                # Found an entry like option[index1] but no dependent keyword
                # indicating which index should be used.
                if cls.debuglevel > 0: dprint("found userlist_params but no dependent index")
                for option, text in options.iteritems():
                    param = cls.findUserListParamStartOf(section, option)
                    if param is not None:
                        userlist_params.add(param)
            
            # Wait to update the depdendent keywords until after everything in
            # the section has been converted.
            if userlist_params:
                if cls.debuglevel > 0: dprint("userlist_params: %s" % str(userlist_params))
                for param in userlist_params:
                    if param.keyword in d:
                        index = d[param.keyword]
                    else:
                        # the dependent keyword doesn't exist, probably because
                        # a corrupted input file.  Need to make a guess at the
                        # index based on what's defined.
                        index = None
                        for keyword in param.dependent_keywords:
                            if cls.debuglevel > 0: dprint("Found %s: %s" % (keyword, d[keyword]))
                            for option, value in d.iteritems():
                                if option.startswith(keyword) and '[' in option:
                                    if keyword in d and value == d[keyword]:
                                        i = option.find('[')
                                        index = option[i+1:len(option)-1]
                                        break
                            if index is not None:
                                break
                        if cls.debuglevel > 0: dprint("Index = %s" % index)
                    cls.setDefaultsForUserList(section, option, param, index, d)
                
            cls.user[section] = d
    
    @classmethod
    def setDefaultsForUserList(cls, section, option, param, index, d):
        for option in param.dependent_keywords:
            dep_option = "%s[%s]" % (option, index)
            try:
                dep_val = d[dep_option]
                d[option] = dep_val
                if cls.debuglevel > 0: dprint("Set userlist dependency %s = %s = %s" % (option, dep_option, dep_val))
            except KeyError:
                # If the ini file is malformed and doesn't have an
                # entry for this index, use the default value.
                dependent_param = cls.findParam(section, option)
                dep_val = dependent_param.default
                if cls.debuglevel > 0: dprint("Set userlist dependency to default: %s = %s = %s" % (option, dep_option, dep_val))
                d[option] = dep_val
        
    
    @classmethod
    def convertConfig(cls):
        # Need to copy dict to temporary one, because BadThings happen
        # while trying to update a dictionary while iterating on it.
        if cls.debuglevel > 0: dprint("before: %s" % cls.user)
        if cls.debuglevel > 0: dprint("name_hierarchy: %s" % cls.name_hierarchy)
        if cls.debuglevel > 0: dprint("params: %s" % cls.params)
        sections = cls.user.keys()
        for section in sections:
            cls.convertSection(section)
        if cls.debuglevel > 0: dprint("after: %s" % cls.user)

        # Save a copy so we can tell if the user changed anything.  Can't
        # use copy.deepcopy(cls.user) anymore because wx.Font objects
        # are now param types and get a type exception trying to deepcopy them.
        saved = {}
        for section, options in cls.user.iteritems():
            saved[section] = {}
            for option, val in options.iteritems():
                try:
                    saved[section][option] = copy.deepcopy(val)
                except Exception, e:
                    # wx swig objects can't be deepcopied, but they are also
                    # unlikely to be modified in place, so setting the saved
                    # value to the same reference is probably OK.
                    #dprint(str(e))
                    saved[section][option] = val
        cls.save_user = saved
            
    @classmethod
    def isUserConfigChanged(cls):
        d = cls.user
        saved = cls.save_user
        for section, options in d.iteritems():
            if cls.debuglevel > 0: dprint("checking section %s" % section)
            if section not in saved:
                # If we have a new section that didn't exist when we
                # loaded the file, something's changed
                if cls.debuglevel > 0: dprint("  new section %s!  Change needs saving." % (section))
                return True
            for option, val in options.iteritems():
                if option not in saved[section]:
                    # We have a new option in an existing section.
                    # It's changed.
                    if cls.debuglevel > 0: dprint("  new option %s in section %s!  Change needs saving." % (option, section))
                    return True
                if val != saved[section][option]:
                    # The value itself has changed.
                    if cls.debuglevel > 0: dprint("  new value %s for %s[%s]." % (val, section, option))
                    return True
                if cls.debuglevel > 0: dprint("  nope, %s[%s] is still %s" % (section, option, val))
        # For completeness, we should check to see if an option has
        # been removed, but currently the interface doesn't provide
        # that ability.
        if cls.debuglevel > 0: dprint("nope, user configuration hasn't changed.")
        return False

    @classmethod
    def configToText(cls):
        if cls.debuglevel > 0: dprint("Saving user configuration: %s" % cls.user)
        lines = ["# Automatically generated file!  Do not edit -- use one of the following",
                 "# files instead:",
                 "#",
                 "# peppy.cfg        For general configuration on all platforms",
                 "# [platform].cfg   For configuration on a specific platform, where [platform]",
                 "#                  is one of the platforms returned by the command",
                 "#                  python -c 'import platform; print platform.system()'",
                 "# [machine].cfg    For configuration on a specific machine, where [machine]",
                 "#                  is the hostname as returned by the command",
                 "#                  python -c 'import platform; print platform.node()'",
                 "",
                 ]
        
        sections = cls.user.keys()
        sections.sort()
        for section in sections:
            options = cls.user[section]
            printed_section = False # flag to indicate if need to print header
            if options:
                keys = options.keys()
                keys.sort()
                for option in keys:
                    val = options[option]
                    param = cls.findParam(section, option)
                    if not printed_section:
                        lines.append("[%s]" % section)
                        printed_section = True
                    if param is None:
                        # No param means that the section doesn't correspond
                        # to any loaded class, probably meaning that it was
                        # a dynamically loaded class that just wasn't loaded
                        # this time.  So, save the raw text since we don't
                        # have a param to use to convert.
                        lines.append("%s = %s" % (option, val))
                    else:
                        lines.append("%s = %s" % (option, param.valueToText(val)))
                lines.append("")
        text = os.linesep.join(lines)
        if cls.debuglevel > 0: dprint(text)
        return text
        

class PrefsProxy(debugmixin):
    """Dictionary-like object to provide global prefs to a class.

    Implements a dictionary that returns a value for a keyword based
    on the class hierarchy.  Each class will define a group of
    prefs and default values for each of those prefs.  The class
    hierarchy then defines the search order if a setting is not found
    in a child class -- the search proceeds up the class hierarchy
    looking for the desired keyword.
    """
    debuglevel=0
    
    def __init__(self,hier):
        names=[k.__name__ for k in hier]
        self.__dict__['_startSearch']=names[0]
        GlobalPrefs.addHierarchy(names[0], hier, names)
        GlobalPrefs.setupHierarchyDefaults(hier)

    def __getattr__(self,name):
        return self._get(name)

    def __call__(self, name):
        return self._get(name)
    
    def _getNameHierarchy(self):
        return GlobalPrefs.name_hierarchy[self.__dict__['_startSearch']]

    def _getClassHierarchy(self):
        return GlobalPrefs.class_hierarchy[self.__dict__['_startSearch']]

    def _get(self, name, user=True, default=True):
        d = self._findNameInHierarchy(name, user, default)
        return d[name]
    
    def _getAllSubscripts(self, name, default_key=None, user=True, default=True):
        """Get all subscripts of a given classpref
        
        Arrays are allowed in config files; they are specified like:
        
          [Test0]
          entry = 1
          entry[one] = 1
          entry[two] = 2
          entry_index = one
        
        This method, given the name 'entry' will return an array containing
        ['one', 'two'].  Note that the default is specified by the name
        without a subscript.
        
        This relies on the fact that the default entry is present.  If the
        default entry is not found, or if more than one entry has the same
        value, this method will not work.  The other way to specify the keyword
        used in the UserListParamStart parameter.  This parameter always
        stores the keyword that it then used as the default index.
        
        If no subscripts are present, a fake subscript named 'default' will be
        created and returned.
        
        @param name: keyword to look for subscripts
        
        @param default_key: keyword to be used to lookup default subscript
        
        @returns: tuple where the first element is the array containing all
        subscripts of that keyword found and the second element is the index
        of the default subscript
        """
        #dprint(name)
        if default_key is not None:
            d = self._findNameInHierarchy(default_key, user, default)
            default_subscript = d[default_key]
            default_value = None
        else:
            d = self._findNameInHierarchy(name, user, default)
            default_value = d[name]
            default_subscript = ""
        match = name + "["
        count = len(match)
        subscripts = []
        for key in d.keys():
            #dprint(key)
            if key.startswith(match) and key[-1] == ']':
                subscript = key[count:-1]
                subscripts.append(subscript)
                if default_value is not None and d[key] == default_value:
                    default_subscript = subscript
        if subscripts:
            subscripts.sort()
            try:
                default_index = subscripts.index(default_subscript)
            except ValueError:
                default_index = 0
        else:
            subscripts = ['default']
            default_index = 0
        return subscripts, default_index
    
    def _findNameInHierarchy(self, name, user=True, default=True):
        klasses=GlobalPrefs.name_hierarchy[self.__dict__['_startSearch']]
        if user:
            d=GlobalPrefs.user
            for klass in klasses:
                #assert self.dprint("checking %s for %s in user dict %s" % (klass, name, d[klass]))
                if klass in d and name in d[klass]:
                    if klass not in GlobalPrefs.convert_already_seen:
                        self.dprint("warning: GlobalPrefs[%s] not converted yet." % klass)
                        GlobalPrefs.convertSection(klass)
                        d = GlobalPrefs.user
                    return d[klass]
        if default:
            d=GlobalPrefs.default
            for klass in klasses:
                #assert self.dprint("checking %s for %s in default dict %s" % (klass, name, d[klass]))
                if klass in d and name in d[klass]:
                    return d[klass]

        klasses=GlobalPrefs.class_hierarchy[self.__dict__['_startSearch']]
        for klass in klasses:
            #assert self.dprint("checking %s for %s in default_classprefs" % (klass,name))
            if hasattr(klass,'default_classprefs') and name in klass.default_classprefs:
                return klass.default_classprefs
        raise AttributeError("%s not found in %s.classprefs" % (name, self.__dict__['_startSearch']))
    
    def _getDependentKeywords(self, index_keyword):
        """Get the dependent keywords given the keyword of a UserListStartParam
        
        The dependent keywords are list items that are indexed by the given
        keyword.  For example, the indexing keyword below is "entry_index" and
        the dependent keyword is "entry"
        
          [Test0]
          entry = 1
          entry[one] = 1
          entry[two] = 2
          entry_index = one
        """
        klasses=GlobalPrefs.class_hierarchy[self.__dict__['_startSearch']]
        for klass in klasses:
            #dprint("checking %s for %s in default_classprefs" % (klass,name))
            if hasattr(klass,'default_classprefs'):
                for param in klass.default_classprefs:
                    if param.keyword == index_keyword:
                        #dprint(param.dependent_keywords)
                        return param.dependent_keywords
        return []
    
    def _updateDependentKeywords(self, index_keyword):
        """Updates the default values in the dependent keywords.
        
        In a UserListStartParam list, default values are placed in keywords
        without a subscript, so for example below:
        
          [Test0]
          entry = 1
          entry[one] = 1
          entry[two] = 2
          entry_index = one
        
        calling this method with the index keyword of "entry_index" will cause
        the value of "entry[one]" to be placed in "entry".
        """
        dependent_keywords = self._getDependentKeywords(index_keyword)
        index = self._get(index_keyword)
        for keyword in dependent_keywords:
            keyword_sub = "%s[%s]" % (keyword, index)
            val = self._get(keyword_sub)
            self._set(keyword, val)
            #dprint("Set %s = %s" % (keyword_sub, self._get(keyword)))

    def __setattr__(self,name,value):
        """Set function including special case to remove a list item if the
        value is C{None}.
        """
        index = self.__dict__['_startSearch']
        if value is None and '[' in name and ']' in name:
            if name in GlobalPrefs.user[index]:
                del GlobalPrefs.user[index][name]
            # FIXME: pretty sure this isn't needed
#            if name in GlobalPrefs.default[index]:
#                #dprint("Deleting default value of %s" % name)
#                del GlobalPrefs.default[index][name]
        else:
            GlobalPrefs.user[index][name]=value

    _set = __setattr__

    def _del(self, name):
        del GlobalPrefs.user[self.__dict__['_startSearch']][name]
        
    def _getValue(self,klass,name):
        d=GlobalPrefs.user
        if klass in d and name in d[klass]:
            return d[klass][name]
        d=GlobalPrefs.default
        if klass in d and name in d[klass]:
            return d[klass][name]
        return None
    
    def _getDefaults(self):
        return GlobalPrefs.default[self.__dict__['_startSearch']]

    def _getUser(self):
        return GlobalPrefs.user[self.__dict__['_startSearch']]

    def _getAll(self):
        d=GlobalPrefs.default[self.__dict__['_startSearch']].copy()
        d.update(GlobalPrefs.user[self.__dict__['_startSearch']])
        return d

    def _getList(self,name):
        vals=[]
        klasses=GlobalPrefs.name_hierarchy[self.__dict__['_startSearch']]
        for klass in klasses:
            val=self._getValue(klass,name)
            if val is not None:
                if isinstance(val,list) or isinstance(val,tuple):
                    vals.extend(val)
                else:
                    vals.append(val)
        return vals

    def _getMRO(self):
        return [cls for cls in GlobalPrefs.class_hierarchy[self.__dict__['_startSearch']]]


class ClassPrefsMetaClass(type):
    def __init__(cls, name, bases, attributes):
        """Add prefs attribute to class attributes.

        All classes of the created type will point to the same
        prefs object.  Perhaps that's a 'duh', since prefs is a
        class attribute, but it is worth the reminder.  Everything
        accessed through self.prefs changes the class prefs.
        Instance prefs are maintained by the class itself.
        """
        #dprint('Bases: %s' % str(bases))
        expanded = [cls]
        for base in bases:
            expanded.extend(getClassHierarchy(base))
        #dprint('New bases: %s' % str(expanded))
        # Add the prefs class attribute
        cls.classprefs = PrefsProxy(expanded)


class InstancePrefs(debugmixin):
    default_prefs = []
    
    @classmethod
    def findParam(cls, section, option):
        hier = cls.__mro__
        for cls in hier:
            #dprint("checking %s" % cls)
            if 'default_prefs' not in dir(cls):
                continue
            for param in cls.default_prefs:
                if param.keyword == option:
                    return param
        return None
    
    def iterPrefs(self):
        hier = self.__class__.__mro__
        for cls in hier:
            #dprint("checking %s" % cls)
            if 'default_prefs' not in dir(cls):
                continue
            for param in cls.default_prefs:
                yield param
    
    def setDefaultPrefs(self):
        for param in self.iterPrefs():
            setattr(self, param.keyword, param.default)
    
    def isConfigChanged(self):
        for param in self.iterPrefs():
            if param.default != getattr(self, param.keyword):
                return True
        return False

    def readConfig(self, fh):
        self.setDefaultPrefs()
        cfg=ConfigParser()
        cfg.optionxform=str
        cfg.readfp(fh)
        for section in cfg.sections():
            for option, text in cfg.items(section):
                param = self.findParam(section, option)
                try:
                    if param is not None:
                        val = param.textToValue(text)
                        if self.debuglevel > 0: dprint("Converted %s to %s(%s) for %s[%s]" % (text, val, type(val), section, option))
                    else:
                        val = text
                    setattr(self, option, val)
                except Exception, e:
                    eprint("Error converting %s in section %s: %s" % (option, section, str(e)))

    def configToText(self):
        seen = {}
        lines = []
        
        hier = self.__class__.__mro__
        for cls in hier:
            #dprint("checking %s" % cls)
            if 'default_prefs' not in dir(cls):
                continue
            section = cls.__name__
            printed_section = False # flag to indicate if need to print header
            for param in cls.default_prefs:
                if param.keyword not in seen:
                    if not printed_section:
                        lines.append("[%s]" % section)
                        printed_section = True
                    val = getattr(self, param.keyword)
                    lines.append("%s = %s" % (param.keyword, param.valueToText(val)))
            lines.append("")
        text = os.linesep.join(lines)
        if self.debuglevel > 0: dprint(text)
        return text


class LocalPrefs(object):
    pass

class ClassPrefs(object):
    """Base class to extend in order to support class prefs.

    Uses the L{ClassPrefsMetaClass} to provide automatic support
    for the prefs class attribute.
    """
    __metaclass__ = ClassPrefsMetaClass
    
    #: Tab in the preferences dialog in which the object will show up
    preferences_tab = "Misc"
    
    #: Alternate label to be used instead of the class name in the preferences list
    preferences_label = None
    
    #: Sorting weight (from 0 to 1000) if you want to group items out of the ordinary the dictionary sort order
    preferences_sort_weight = 500
    
    @classmethod
    def classprefsGetClassLabel(cls):
        if cls.preferences_label is not None:
            return cls.preferences_label
        return cls.__name__
    
    def classprefsCopyToLocals(self):
        if not hasattr(self, 'locals'):
            self.locals = LocalPrefs()
        hier = self.classprefs._getMRO()
        #dprint(hier)
        updated = {}
        for cls in hier:
            if 'default_classprefs' not in dir(cls):
                continue
            for param in cls.default_classprefs:
                if param.local:
                    #dprint("setting instance var %s to %s" % (param.keyword, self.classprefs._get(param.keyword)))
                    setattr(self.locals, param.keyword, self.classprefs._get(param.keyword))

    def classprefsCopyFromLocals(self):
        if not hasattr(self, 'locals'):
            raise AttributeError("local copy of classprefs doesn't exist in %s!" % self)
        hier = self.classprefs._getMRO()
        #dprint(hier)
        updated = {}
        for cls in hier:
            if 'default_classprefs' not in dir(cls):
                continue
            for param in cls.default_classprefs:
                if param.local:
                    #dprint("setting classpref %s to %s" % (param.keyword, self.classprefs._get(param.keyword)))
                    self.classprefs._set(param.keyword, getattr(self.locals, param.keyword))
    
    @classmethod
    def classprefsSetDefaults(cls, new_locals):
        """Update the class defaults from the locals
        """
        hier = cls.classprefs._getMRO()
        #dprint(hier)
        updated = {}
        for cls in hier:
            if 'default_classprefs' not in dir(cls):
                continue
            for param in cls.default_classprefs:
                if param.local and param.keyword in new_locals:
                    #dprint("setting classpref %s to %s" % (param.keyword, self.classprefs._get(param.keyword)))
                    cls.classprefs._set(param.keyword, new_locals[param.keyword])
    
    @classmethod
    def classprefsOverrideSubclassDefaults(cls, new_locals):
        """Override settings of this class and all subclasses
        
        Remove all the user values from GlobalPrefs of items that would
        override these class settings definitions.  All subclasses of this
        class will have their settings removed so that this class becomes the
        default value for all of its subclasses.
        
        This is used to change the settings of FundamentalMode so that
        subclasses like PythonMode or CPlusPlusMode will take their settings
        from Fundamental mode.
        """
        cls.classprefsSetDefaults(new_locals)
        #dprint("fund: %s" % GlobalPrefs.user)
        #dprint("defaults: %s" % GlobalPrefs.default)
        #dprint("name: %s" % GlobalPrefs.name_hierarchy)
        parent = cls.__name__
        for name, hier in GlobalPrefs.name_hierarchy.iteritems():
            if parent in hier:
                if name == parent:
                    # Override fundamental mode defaults with user settings
                    #dprint("before: %s: %s" % (name, GlobalPrefs.user[name]))
                    for k in new_locals.keys():
                        if k in GlobalPrefs.default[name] and GlobalPrefs.default[name] != new_locals[k]:
                            GlobalPrefs.user[name][k] = new_locals[k]
                    #dprint("after: %s: %s" % (name, GlobalPrefs.user[name]))
                else:
                    # erase user settings so that fundamental mode settings
                    # will show through
                    #dprint("before: %s: %s" % (name, GlobalPrefs.user[name]))
                    for k in new_locals.keys():
                        if k in GlobalPrefs.user[name]:
                            del GlobalPrefs.user[name][k]
                    #dprint("after: %s: %s" % (name, GlobalPrefs.user[name]))

    def classprefsDictFromLocals(self):
        """Return a dict copy of the local settings"""
        copy = {}
        for k in dir(self.locals):
            if not k.startswith('_'):
                copy[k] = getattr(self.locals, k)
        return copy
    
    def classprefsUpdateLocals(self, new_locals):
        """Update the locals with new values
        
        This updates the locals object with the name/value pairs specified in
        the passed-in dict.
        """
        for name, value in new_locals.iteritems():
            if hasattr(self.locals, name):
                setattr(self.locals, name, value)
    
    def classprefsFindParam(self, keyword):
        """Find the Param instance corresponding to the keyword.
        
        Search each default_classprefs going up the class hierarchy for a param
        with the specified keyword.
        
        This currently uses an inefficient linear search, so if this is called
        a lot, it will probably be slow.
        
        @param keyword: string to search for
        @return: param instance
        @raises: IndexError if not found
        """
        if not hasattr(self, 'locals'):
            raise AttributeError("local copy of classprefs doesn't exist in %s!" % self)
        hier = self.classprefs._getMRO()
        #dprint(hier)
        updated = {}
        for cls in hier:
            if 'default_classprefs' not in dir(cls):
                continue
            for param in cls.default_classprefs:
                if param.keyword == keyword:
                    #dprint("found %s in %s" % (param.keyword, cls))
                    return param
        raise IndexError("keyword %s not found in default_classprefs in any class in the MRO of %s" % (param.keyword, self))


class PrefPanel(ScrolledPanel, debugmixin):
    """Panel that shows ui controls corresponding to all preferences
    """

    def __init__(self, parent, obj):
        ScrolledPanel.__init__(self, parent, -1, size=(500,-1),
                               pos=(9000,9000))
        self.parent = parent
        self.obj = obj
        
        self.ctrls = {}
        self.orig = {}
        
        # self.sizer = wx.GridBagSizer(2,5)
        self.sizer = wx.BoxSizer(wx.VERTICAL)
        self.create()
        self.SetSizer(self.sizer)
        self.Layout()

    def Layout(self):
        ScrolledPanel.Layout(self)
        self.SetupScrolling()
        self.Scroll(0,0)
    
    class gridstate(object):
        """Private class used to keep track of a GridBagSizer as items are being
        added to it.
        """
        def __init__(self):
            self.row = 0
            self.col = 0
            self.grid = wx.GridBagSizer(2,5)
            self.name = ""
    
    @staticmethod
    def populateGrid(parent, sizer, name, params, existing_keywords, ctrls, orig, default_getter=None):
        """Static method to populate some sizer with classprefs.
        
        This is a static method because it can be used by outside functions
        that use params but aren't classprefs.  For example, generic dialog
        boxes can be created by setting up a list of params, and then
        populating the dialog with the controls.
        
        parent: parent of all the controls to be generated
        sizer: sizer in which to add all the grid sizer
        name: name of the StaticBox that wraps the grid sizer
        params: list of Params to create user interface elements
        existing_keywords: dict containing keywords whose elements have been created by a previous call to populateGrid and therefore won't be created during this invocation
        ctrls: dict keyed on param to add the created controls
        orig: dict keyed on param to store the original values
        default_getter: optional functor to return a default value for the keyword
        """
        sections = PrefPanel.getSections(params, name)
        for name, section_params in sections:
            for item in PrefPanel.getSectionItems(parent, name, section_params, existing_keywords, ctrls, orig, default_getter):
                sizer.Add(item, 0, wx.EXPAND)
    
    @staticmethod
    def getSectionItems(parent, name, params, existing_keywords, ctrls, orig, default_getter):
        """Get items to be added to the panel
        
        Items are usually StaticBoxSizers, each of which contains a grid of
        controls representing the classprefs for the class.
        """
        local_params, global_params = PrefPanel.getLocalAndGlobalParams(params, existing_keywords)
        
        section_items = []
        if global_params or local_params:
            box = wx.StaticBox(parent, -1, name)
            bsizer = wx.StaticBoxSizer(box, wx.VERTICAL)
            section_items.append(bsizer)
        
        if global_params:
            items = PrefPanel.getGridItems(parent, global_params, existing_keywords, ctrls, orig, default_getter)
            for item in items:
                #dprint(item.name)
                if item.name:
                    box2 = wx.StaticBox(parent, -1, item.name)
                    bsizer2 = wx.StaticBoxSizer(box2, wx.VERTICAL)
                    bsizer2.Add(item.grid, 0, wx.EXPAND)
                    bsizer.Add(bsizer2, 0, wx.EXPAND)
                else:
                    bsizer.Add(item.grid, 0, wx.EXPAND)
            
        if local_params:
            items = PrefPanel.getGridItems(parent, local_params, existing_keywords, ctrls, orig, default_getter)
            label = GenStaticText(parent, -1, "\n" + _("Local settings (each view can have different values for these settings)"))
            label.SetToolTip(wx.ToolTip("Each view maintains its own value for each of the settings below.  Changes made here will be used as default values for these settings the next time the applications starts and when opening new views.  Additionally, the settings can be applied to existing views when finished with the Preferences dialog."))
            bsizer.Add(label, 0, wx.EXPAND)
            for item in items:
                bsizer.Add(item.grid, 0, wx.EXPAND)
        
        return section_items

    @staticmethod
    def getSections(params, default_name):
        """Split list of params into sections delimited by ParamSection
        instances.
        
        @returns: list of tuples, where the first element is the name of the
        section and the second is the list of params in that section.
        
        """
        sections = []
        current_name = default_name
        current_params = []
        for param in params:
            if param.isSectionHeader():
                sections.append((current_name, current_params))
                current_name = param.getStaticBoxTitle()
                current_params = []
            else:
                current_params.append(param)
        sections.append((current_name, current_params))
        #dprint(sections)
        return sections

    @staticmethod
    def getLocalAndGlobalParams(params, existing_keywords):
        """Split list of params into two lists, one with the classwide
        classprefs, and one with the instanceprefs.
        
        """
        local_params = []
        global_params = []
        for param in params:
            if param.keyword in existing_keywords:
                # Don't put another control if it exists in a superclass
                continue
            
            if param.isSuperseded():
                # Force the keyword to be skipped in the UI for a superclass
                existing_keywords[param.keyword] = True
                continue
            
            if not param.isVisible():
                # Don't use control if it is a hidden parameter
                continue

            if param.local:
                local_params.append(param)
            else:
                global_params.append(param)
        
        return local_params, global_params

    @staticmethod
    def getGridItems(parent, params, existing_keywords, ctrls, orig, default_getter=None):
        grid = PrefPanel.gridstate()
        items = []
        needs_init = []
        processing_dependent_keywords = False
        for param in params:
            #dprint(param)
            existing_keywords[param.keyword] = True
            if param.isDependentKeywordStart():
                if len(grid.grid.GetChildren()) > 0:
                    items.append(grid)
                grid = PrefPanel.gridstate()
                grid.name = param.getStaticBoxTitle()
                processing_dependent_keywords = True
                needs_init.append(param)
            elif param.isDependentKeywordEnd():
                items.append(grid)
                grid = PrefPanel.gridstate()
                processing_dependent_keywords = False
                continue
            width = 1
            title = param.getLabel(parent)
            if title is not None:
                if grid.grid.CheckForIntersectionPos((grid.row,grid.col), (1,width)):
                    dprint("found overlap for %s at (%d,%d)" % (param.keyword, grid.row, grid.col))
                else:
                    grid.grid.Add(title, (grid.row,grid.col), flag=wx.ALIGN_CENTER_VERTICAL)
                grid.col += 1
            else:
                width = 2
            if param.wide:
                if isinstance(param.wide, int):
                    width += param.wide
                else:
                    width += 2
                if param.fullwidth:
                    grid.grid.AddGrowableCol(grid.col + width - 1)

            if param.isSettable():
                ctrl = param.getCtrl(parent)
                ctrls[param] = ctrl
                param.setCallback(ctrl, ctrls)
                if param.help:
                    ctrl.SetToolTipString(param.help)
                if grid.grid.CheckForIntersectionPos((grid.row,grid.col), (1,width)):
                    dprint("found overlap for control of %s at (%d,%d)" % (param.keyword, grid.row, grid.col))
                else:
                    grid.grid.Add(ctrl, (grid.row,grid.col), (1,width), flag=wx.EXPAND)
                grid.col += width
                
                if not processing_dependent_keywords:
                    # Dependent keywords will be initialized when the
                    # UserListParamStart param is initialized.
                    needs_init.append(param)
                
            if not param.next_on_same_line:
                grid.row += 1
                grid.col = 0
        
        for param in needs_init:
            param.setInitialDefaults(ctrls[param], ctrls, default_getter, orig)
        
        if len(grid.grid.GetChildren()) > 0:
            items.append(grid)
        return items
    
    def ensureParamVisible(self, param_name):
        for param, ctrl in self.ctrls.iteritems():
            if param.keyword == param_name:
                wx.CallAfter(self.ScrollChildIntoView, ctrl)
                break

    def create(self):
        """Create the list of classprefs, organized by MRO.
        
        Creates a group of StaticBoxes, each one representing the classprefs
        of a class in the MRO (method resolution order).
        """
        row = 0
        focused = False
        hier = [c for c in self.obj.classprefs._getMRO() if 'default_classprefs' in dir(c)]
        self.dprint(hier)
        first = True
        existing_keywords = {}
        for cls in hier:
            if first:
                name = cls.__name__
                first = False
            else:
                name = "Inherited from %s" % cls.__name__
            self.populateGrid(self, self.sizer, name, cls.default_classprefs,
                              existing_keywords, self.ctrls, self.orig,
                              self.obj.classprefs)
        
    def update(self):
        """Update the class with the changed preferences.
        
        @return: dict of local settings that have been modified.
        """
        hier = self.obj.classprefs._getMRO()
        setter = self.obj.classprefs._set
        self.dprint(hier)
        updated = {}
        locals = {}
        for cls in hier:
            if 'default_classprefs' not in dir(cls):
                continue
            
            for param in cls.default_classprefs:
                if param in updated:
                    # Don't update with value in superclass
                    continue

                # It's possible that a param won't have an associated control
                # because the param is handled by a subclass.  Params are
                # class attributes, and if a param of the same name is
                # overridden by a subclass, the control will only appear in
                # the subclass, not any superclasses.  In addition, only deal
                # with those controls that are settable
                if param in self.ctrls and param.isSettable():
                    param.updateIfModified(self.ctrls, self.orig, setter, locals)
                    updated[param] = True
        return locals


class InstancePanel(PrefPanel):
    """Subclass of PrefPanel to manage InstancePrefs objects"""
    debuglevel = 1
    
    def getValue(self, keyword):
        return getattr(self.obj, keyword)
    
    def create(self):
        """Create the list of classprefs, organized by MRO.
        
        Creates a group of StaticBoxes, each one representing the classprefs
        of a class in the MRO (method resolution order).
        """
        row = 0
        focused = False
        hier = [c for c in self.obj.__class__.__mro__ if 'default_prefs' in dir(c)]
        self.dprint(hier)
        first = True
        existing_keywords = {}
        for cls in hier:
            if first:
                name = cls.__name__
                first = False
            else:
                name = "Inherited from %s" % cls.__name__
            self.populateGrid(self, self.sizer, name, cls.default_prefs,
                              existing_keywords, self.ctrls, self.orig,
                              self.getValue)
    
    def setValue(self, keyword, value):
        setattr(self.obj, keyword, value)
        
    def update(self):
        """Update the class with the changed preferences.
        
        @return: dict of local settings that have been modified.
        """
        updated = {}
        for param in self.obj.iterPrefs():
            if param in self.ctrls and param.isSettable():
                param.updateIfModified(self.ctrls, self.orig, self.setValue)
                updated[param] = True
        return updated


class PrefClassList(ColumnAutoSizeMixin, wx.ListCtrl, debugmixin):
    def __init__(self, parent, classes):
        wx.ListCtrl.__init__(self, parent, size=(200,400), style=wx.LC_REPORT)
        ColumnAutoSizeMixin.__init__(self)

        self.setIconStorage()
        
        self.skip_verify = False

        self.createColumns()
        self.reset(classes)

    def setIconStorage(self):
        pass

    def appendItem(self, name):
        self.InsertStringItem(sys.maxint, name)

    def setItem(self, index, name):
        self.SetStringItem(index, 0, name)

    def createColumns(self):
        self.InsertSizedColumn(0, "Class")
    
    def reset(self, classes):
        index = 0
        list_count = self.GetItemCount()
        self.class_names = []
        self.class_map = {}
        for cls in classes:
            label = cls.classprefsGetClassLabel()
            self.class_names.append(label)
            self.class_map[label] = cls
            if index >= list_count:
                self.appendItem(label)
            else:
                self.setItem(index, label)
            index += 1

        if index < list_count:
            for i in range(index, list_count):
                # always delete the first item because the list gets
                # shorter by one each time.
                self.DeleteItem(index)
        self.SetColumnWidth(0, wx.LIST_AUTOSIZE)
    
    def clearSelected(self):
        """Clear any selected items from the list.
        
        Note that each call to SetItemState generates a EVT_LIST_ITEM_SELECTED
        event, so the callback processing should be turned off before calling
        this method.
        """
        index = 0
        list_count = self.GetItemCount()
        if index < list_count:
            self.SetItemState(index, 0, wx.LIST_STATE_SELECTED)
            index += 1

    def getClass(self, index=-1):
        if index == -1:
            index = self.GetNextItem(-1, wx.LIST_NEXT_ALL, wx.LIST_STATE_SELECTED)
        if index == -1:
            index = 0
        return self.class_map[self.class_names[index]]

    def ensureClassVisible(self, cls):
        self.clearSelected()
        try:
            label = cls.classprefsGetClassLabel()
            index = self.class_names.index(label)
            self.SetItemState(index, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
            self.EnsureVisible(index)
        except:
            # item not in list. skip it.
            pass

class PrefPage(wx.SplitterWindow):
    """Notebook page to hold preferences for one notebook tab"""
    
    def __init__(self, parent, classes, *args, **kwargs):
        wx.SplitterWindow.__init__(self, parent, -1, *args, **kwargs)
        self.SetMinimumPaneSize(50)
        
        self.process_callback = False
        
        self.pref_panels = {}
        list = self.createList(classes)
        dum = wx.Window(self, -1)
        self.SplitVertically(list, dum, -500)
        list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.OnItemSelected, list)

    def OnItemSelected(self, evt):
        if self.process_callback:
            index = evt.GetIndex()
            list = evt.GetEventObject()
            if index >= 0:
                cls = list.getClass(index)
                self.changePanel(cls)
        evt.Skip()

    def createList(self, classes):
        """Create the ListCtrl that holds the list of classes"""
        list = PrefClassList(self, classes)
        return list
        
    def showClass(self, cls=None):
        """Make sure the requested class is highlighted and its pref panel
        is shown."""
        self.process_callback = False
        self.GetWindow1().ensureClassVisible(cls)
        self.process_callback = True
    
    def getPanel(self, cls=None):
        """Get or create the requested class's pref panel"""
        if cls is None:
            cls = self.GetWindow1().getClass()
        if cls.__name__ in self.pref_panels:
            pref = self.pref_panels[cls.__name__]
        else:
            pref = PrefPanel(self, cls)
            self.pref_panels[cls.__name__] = pref
        return pref
    
    def changePanel(self, cls=None, scroll_to=None):
        """Change the currently shown pref panel to the requested"""
        old = self.GetWindow2()
        if cls is None:
            cls = self.GetWindow1().getClass()
        pref = self.getPanel(cls)
        self.ReplaceWindow(old, pref)
        self.showClass(cls)
        old.Hide()
        pref.Show()
        pref.Layout()
        pref.ensureParamVisible(scroll_to)

    def applyPreferences(self):
        """On an OK from the dialog, process any updates to the plugins"""
        # Look at all the panels that have been created: this gives us
        # an upper bound on what may have changed.
        all_locals = {}
        for cls, pref in self.pref_panels.iteritems():
            locals = pref.update()
            if locals:
                # use the actual object as the key rather than the name of
                # the object
                all_locals[pref.obj] = locals
        return all_locals


class PrefDialog(wx.Dialog):
    dialog_title = "Preferences"
    static_title = "This is a placeholder for the Preferences dialog"
    
    def __init__(self, parent, obj, title=None, scroll_to=None):
        if title is None:
            title = self.dialog_title
        wx.Dialog.__init__(self, parent, -1, title,
                           size=(700, 500), pos=wx.DefaultPosition, 
                           style=wx.DEFAULT_DIALOG_STYLE|wx.RESIZE_BORDER)

        self.obj = obj

        sizer = wx.BoxSizer(wx.VERTICAL)

        if self.static_title:
            label = GenStaticText(self, -1, self.static_title)
            sizer.Add(label, 0, wx.ALIGN_CENTRE|wx.ALL, 5)

        self.createClassMap()
        
        self.notebook = wx.Notebook(self)
        sizer.Add(self.notebook, 1, wx.EXPAND)
        self.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.OnTabChanged)
        
        # Sort the names so that Misc and Plugins are always last
        last = []
        names = self.tab_map.keys()
        for name in ['Misc', 'Plugins']:
            if name in names:
                names.remove(name)
                last.append(name)
        names.sort()
        names.extend(last)
        
        self.tab_to_page = {}
        count = 0
        for tab in names:
            splitter = self.createPage(tab, self.tab_map[tab])
            self.notebook.AddPage(splitter, tab)
            self.tab_to_page[tab] = count
            count += 1
        
        btnsizer = wx.StdDialogButtonSizer()
        
        btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        btnsizer.AddButton(btn)

        btn = wx.Button(self, wx.ID_CANCEL)
        btnsizer.AddButton(btn)
        btnsizer.Realize()

        sizer.Add(btnsizer, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)

        self.SetSizer(sizer)
        sizer.Fit(self)

        self.showRequestedPreferences(scroll_to)

        self.Layout()
    
    def showRequestedPreferences(self, scroll_to):
        """Sets the notebook page and initial panel to the preferences of the
        initial object passed to the constructor.
        """
        # It is possible that the object won't be found if its preferences_tab
        # class attribute has been set to None.
        try:
            index = self.getPageIndex(self.obj.__class__)
            cls = self.obj.__class__
        except KeyError:
            index = 0
            cls = None
        self.notebook.SetSelection(index)
        page = self.notebook.GetPage(index)
        page.changePanel(cls, scroll_to)
    
    def OnTabChanged(self, evt):
        #dprint()
        val = evt.GetSelection()
        page = self.notebook.GetPage(val)
        page.changePanel()
        evt.Skip()

    def createClassMap(self):
        classes = getAllSubclassesOf(ClassPrefs)
        # Because getAllSubclassesOf can return multiple classes that
        # really refer to the same class (still don't understand how this
        # is possible) we need to sort on class name and make that the
        # key instead of the class object itself.
        unique = {}
        for cls in classes:
            unique[cls.__name__] = cls
        self.class_map = unique
        classes = unique.values()
        classes.sort(key=lambda x: (x.preferences_sort_weight, x.classprefsGetClassLabel()))
        
        self.tab_map= {}
        self.class_to_tab = {}
        for cls in classes:
            tab = cls.preferences_tab
            
            # preferences_tab = None means that we shouldn't display preferences
            # for this object, so it isn't added to any of the lists.  We have
            # to special-case this in showRequestedPreferences above, however.
            if tab is None:
                continue
            if tab not in self.tab_map:
                self.tab_map[tab] = []
            self.tab_map[tab].append(cls)
            self.class_to_tab[cls.__name__] = tab
        #dprint(self.tab_map)
        #dprint(self.class_to_tab)
    
    def createPage(self, tab, classes):
        splitter = PrefPage(self.notebook, classes)
        return splitter

    def getPageIndex(self, cls):
        tab = self.class_to_tab[cls.__name__]
        index = self.tab_to_page[tab]
        return index

    def applyPreferences(self):
        locals = {}
        # For every notebook page, update the preferences for that notebook and
        # gather any local preferences
        for index in range(self.notebook.GetPageCount()):
            page = self.notebook.GetPage(index)
            page_locals = page.applyPreferences()
            if page_locals:
                locals.update(page_locals)
        return locals

if __name__ == "__main__":
    _ = str
    
    class Test0(ClassPrefs):
        default_classprefs = (
            IntParam("test0", 5),
            UserListParamStart('userlist', ['path1', 'string1'], 'Test of userlist'),
            PathParam('path1', '', fullwidth=True),
            StrParam('string1', '', fullwidth=True),
            UserListParamEnd('userlist'),
            BoolParam('stuff'),
            UserListParamStart('list2', ['path2', 'string2'], "Second test of userlist"),
            PathParam('path2', '', fullwidth=True),
            StrParam('string2', '', fullwidth=True),
            UserListParamEnd('list2'),
            )
    
    class Test1(ClassPrefs):
        default_classprefs = (
            IntParam("test1", 5, local=True),
            FloatParam("testfloat", 6.0),
            IntParam("testbase1", 1, local=True),
            IntParam("testbase2", 2),
            IntParam("testbase3", 3),
            UserListParamStart('userlist', ['path1', 'string1']),
            PathParam('path1', '', fullwidth=True),
            StrParam('string1', '', fullwidth=True),
            UserListParamEnd('userlist'),
            )
        
        def __init__(self):
            self.classprefsCopyToLocals()
    
    class Test2(Test1):
        default_classprefs = (
            IntParam("test1", 7, join=True),
            FloatParam("testfloat", 6.0),
            StrParam("teststr", "BLAH!"),
            IntParam("col1", 1, next_on_same_line=True),
            IntParam("col2", 2),
            IntParam("col1again", 3, label=False),
            )

    class TestA(ClassPrefs):
        default_classprefs = (
            BoolParam("testa"),
            )

    class TestB(TestA):
        default_classprefs = (
            PathParam("testpath"),
            )
        pass

    class TestC(TestA):
        pass
    
    sample = """\
[Test0]
test0 = 1111
path1 = blah
path1[one] = blah
path1[two] = blah2
userlist = two
string1 = stuff
string1[one] = stuff
string1[two] = stuff2
path2 = vvvv
string2 = zzzz
    """
    sample = """\
[Test0]
path1 = ""
path1[New Item] = 
path1[one] = blah
path1[two] = blah2
path2 = "vvvv"
string1 = ""
string1[New Item] = 
string1[one] = stuff
string1[two] = stuff2
string2 = "zzzz"
test0 = 1111
userlist = "New Item"
    """
    fh = StringIO(sample)
    GlobalPrefs.readConfig(fh)
    
    t0 = Test0()
    t1 = Test1()
    t2 = Test2()

    print "at initialization"
    print "t1.classprefs.test1=%s t1.locals.test1=%s" % (t1.classprefs.test1, t1.locals.test1)
    print "t1.classprefs.testbase1=%s t1.locals.testbase1=%s" % (t1.classprefs.testbase1, t1.locals.testbase1)
    print "t2.classprefs.test1=%s t2.locals.test1=%s" % (t2.classprefs.test1, t2.locals.test1)
    print "t2.classprefs.testbase1=%s t2.locals.testbase1=%s" % (t2.classprefs.testbase1, t2.locals.testbase1)
    print

    t1.classprefs.testbase1 = 113
    t1.locals.test1 = 593
    print "after setting t1.classprefs.test1=%s t1.locals.test1=%s" % (t1.classprefs.test1, t1.locals.test1)
    print "t1.classprefs.test1=%s t1.locals.test1=%s" % (t1.classprefs.test1, t1.locals.test1)
    print "t1.classprefs.testbase1=%s t1.locals.testbase1=%s" % (t1.classprefs.testbase1, t1.locals.testbase1)
    print "t2.classprefs.test1=%s t2.locals.test1=%s" % (t2.classprefs.test1, t2.locals.test1)
    print "t2.classprefs.testbase1=%s t2.locals.testbase1=%s" % (t2.classprefs.testbase1, t2.locals.testbase1)
    print

    t1.classprefsCopyToLocals()
    print "after t1.classprefsCopyToLocals"
    print "t1.classprefs.test1=%s t1.locals.test1=%s" % (t1.classprefs.test1, t1.locals.test1)
    print "t1.classprefs.testbase1=%s t1.locals.testbase1=%s" % (t1.classprefs.testbase1, t1.locals.testbase1)
    print "t2.classprefs.test1=%s t2.locals.test1=%s" % (t2.classprefs.test1, t2.locals.test1)
    print "t2.classprefs.testbase1=%s t2.locals.testbase1=%s" % (t2.classprefs.testbase1, t2.locals.testbase1)
    print

    t1.locals.test1 = -888
    t1.classprefsCopyFromLocals()
    print "after setting t1.locals.test1=%d and then t1.classprefsCopyFromLocals" % t1.locals.test1
    print "t1.classprefs.test1=%s t1.locals.test1=%s" % (t1.classprefs.test1, t1.locals.test1)
    print "t1.classprefs.testbase1=%s t1.locals.testbase1=%s" % (t1.classprefs.testbase1, t1.locals.testbase1)
    print "t2.classprefs.test1=%s t2.locals.test1=%s" % (t2.classprefs.test1, t2.locals.test1)
    print "t2.classprefs.testbase1=%s t2.locals.testbase1=%s" % (t2.classprefs.testbase1, t2.locals.testbase1)
    print

    t2.classprefs.testbase1 = 9874
    print "after setting t2.classprefs.testbase1=%s" % t2.classprefs.testbase1
    print "t1.classprefs.test1=%s t1.locals.test1=%s" % (t1.classprefs.test1, t1.locals.test1)
    print "t1.classprefs.testbase1=%s t1.locals.testbase1=%s" % (t1.classprefs.testbase1, t1.locals.testbase1)
    print "t2.classprefs.test1=%s t2.locals.test1=%s" % (t2.classprefs.test1, t2.locals.test1)
    print "t2.classprefs.testbase1=%s t2.locals.testbase1=%s" % (t2.classprefs.testbase1, t2.locals.testbase1)
    print
    
    t1.classprefsFindParam('testbase1')

    app = wx.PySimpleApp()

    dlg = PrefDialog(None, t0)
    dlg.Show(True)

    # Close down the dialog on a button press
    def applyPrefs(evt):
        dlg.applyPreferences()
        text = GlobalPrefs.configToText()
        dprint(text)
        sys.exit()
        
    import sys
    dlg.Bind(wx.EVT_BUTTON, applyPrefs)

    app.MainLoop()
    
