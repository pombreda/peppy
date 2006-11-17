#!/usr/bin/env python
"""
Managing user config files and directories.
"""

import os,types
from ConfigParser import ConfigParser
import cPickle as pickle
from debug import *

__all__ = [ 'HomeConfigDir', 'HierarchalConfig', 'ConfigMixin' ]

def getHomeDir(debug=False):
    """
    Try to find the user home directory, otherwise return current
    directory.  Adapted from
    http://mail.python.org/pipermail/python-list/2005-February/263921.html
    """
    try:
        path1=os.path.expanduser("~")
        if debug: print "path1=%s" % path1
    except:
        path1=""
    try:
        path2=os.environ["HOME"]
        if debug: print "path2=%s" % path2
    except:
        path2=""
    try:
        path3=os.environ["USERPROFILE"]
        if debug: print "path3=%s" % path3
    except:
        path3=""
    try:
        path4 = os.environ.get('HOMEPATH', None)
        if path4 is not None:
            path4 = os.environ['HOMEDRIVE']+homeDir
        if debug: print "path4=%s" % path4
    except:
        path4=""

    # Note that if you are running under Cygwin but using the Windows
    # python, you'll get the unix-style homedir if running under a
    # Cygwin shell, but the Windows 'Documents and Settings' dir if
    # running under standalone Python.
    if os.sys.platform=='win32':
        if os.path.exists(path3):
            return path3
        elif os.path.exists(path4):
            return path4
        # else, fallthrough to regular code
    
    # Cygwin's python shows up with os.sys.platform=='cygwin', so it
    # will default to the unix style homedir.
    if os.path.exists(path1):
        return path1
    elif os.path.exists(path2):
        return path2
    elif os.path.exists(path3):
        return path3
    elif os.path.exists(path4):
        return path4
    else:
        return os.getcwd()

class HomeConfigDir:
    def __init__(self,dirname,create=True,debug=False):
        self.home=getHomeDir(debug)
        if dirname.startswith('.'):
            dirname=dirname[1:]
        if os.sys.platform=='win32':
            self.dot='_'
        else:
            self.dot='.'
        self.dir=os.path.join(self.home,self.dot+dirname)

        self.check(create)

    def check(self,create):
        if not os.path.exists(self.dir):
            if create:
                os.mkdir(self.dir)

    def exists(self,name):
        return os.path.exists(os.path.join(self.dir,name))

    def open(self,name,mode='r'):
        path=os.path.join(self.dir,name)
        fd=open(path,mode)
        return fd

    def loadObject(self,name):
        item=None
        if self.exists(name):
            fd=self.open(name,'rb')
            item=pickle.load(fd)
            fd.close()
        return item

    def saveObject(self,name,item):
        fd=self.open(name,'wb')
        pickle.dump(item,fd)
        fd.close()


def parents(c, seen=None):
    """Python class base-finder from
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


parentclasses={}

def getHierarchy(obj):
    global parentclasses
    
    klass=obj.__class__
    if klass in parentclasses:
        names=parentclasses[klass]
        print "Found class hierarchy: %s" % names
    else:
        names=[k.__name__ for k in parents(klass) if k.__name__ not in ['debugmixin','ConfigMixin','object']]
        print "Created class hierarchy: %s" % names
        parentclasses[klass]=names
    return names

    
class HierarchalConfig(debugmixin):
    """Subclass of the standard ConfigParser to march up the class
    hierarchy of the calling class to find defaults.  Classes are
    searched in method resolution order by the string name of the
    class, which, on one hand, means that classes of the name name in
    different namespaces will map to the same config string; but on
    the other hand means that you get nice short names in the config
    file.

    
    """
    def __init__(self,defaults={},appdefs={},userdefs={}):
        self.appcfg=ConfigParser(defaults)
        self.usercfg=ConfigParser()
        self.setAppDefaults(appdefs)
        self.debuglevel=1

    def setAppDefaults(self,defaults):
        for section,values in defaults.iteritems():
            self.appcfg.add_section(section)
            for option,value in values.iteritems():
                self.appcfg.set(section,option,str(value))

        # Make the config parser case sensitive
        self.appcfg.optionxform=str

    def loadConfig(self,filename):
        processed=self.usercfg.read(filename)
        self.dprint("config files processed: %s" % str(processed))
        self.dprint("  defaults: %s" % self.usercfg.defaults())
        self.dprint("  sections: %s" % self.usercfg.sections())
        for section in self.usercfg.sections():
            self.dprint("  items in %s: %s" % (section,self.usercfg.items(section)))

    def saveConfig(self,filename):
        self.dprint("saving configuration to %s" % filename)
        from cStringIO import StringIO
        fh=StringIO()
        self.usercfg.write(fh)
        print fh.getvalue()

    def get(self,obj,option):
        print "debuglevel=%s" % self.debuglevel
        names=getHierarchy(obj)
        for name in names:
            self.dprint("checking %s for %s in usercfg" % (name,option))
            if self.usercfg.has_section(name) and self.usercfg.has_option(name,option):
                return ConfigParser.get(self.usercfg,name,option)
        for name in names:
            self.dprint("checking %s for %s in appcfg" % (name,option))
            if self.appcfg.has_section(name) and self.appcfg.has_option(name,option):
                return ConfigParser.get(self.appcfg,name,option)
        return None
    
    def getint(self,obj,option):
        val=self.get(obj,option)
        return int(val)

    def setoption(self,obj,param,value):
        names=getHierarchy(obj)
        # Use the first name we find; that's the calling object
        section=names[0]
        if not self.usercfg.has_section(section):
            self.usercfg.add_section(section)
        self.usercfg.set(section,param,str(value))

class ConfigMixin(object):
    def __init__(self,cfg):
        self._hierarchalconfig=cfg
        self._hierarchalconfig.debuglevel=1

    def get(self,param):
        return self._hierarchalconfig.get(self,param)

    def getint(self,param):
        return self._hierarchalconfig.getint(self,param)

    def getboolean(self,param):
        val=self._hierarchalconfig.get(self,param)
        if val is not None and val.lower() in ['1','yes','true','on']:
            return True
        return False

    def setoption(self,param,value):
        self._hierarchalconfig.setoption(self,param,str(value))


if __name__=='__main__':
    print "for platform %s:" % os.sys.platform
    print getHomeDir()
    c=HomeConfigDir(".configprefs",debug=True)
    print "found home dir=%s" % c.dir
    fd=c.open("test.cfg",'w')
    fd.write('blah!!!')
    fd.close()
    nums=[0,1,2,4,6,99]
    c.saveObject("stuff.bin",nums)
    print c.loadObject("stuff.bin")
