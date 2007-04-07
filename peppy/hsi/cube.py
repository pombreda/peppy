# peppy Copyright (c) 2006-2007 Rob McMullen
# Licenced under the GPL; see http://www.flipturn.org/peppy for more info
"""Reading and writing raw HSI cubes.

This class supports reading HSI data cubes (that are stored in raw,
uncompressed formats) using memory mapped file access.
"""

import os,sys,re,random, glob
from cStringIO import StringIO

import utils

from peppy.debug import *

from peppy.trac.core import *
from peppy.iofilter import *

from numpy.core.numerictypes import *
import numpy

# ENVI standard byte order: 0=little endian, 1=big endian
LittleEndian=0
BigEndian=1
if sys.byteorder=='little':
    nativeByteOrder=LittleEndian
else:
    nativeByteOrder=BigEndian
byteordertext=['<','>']


# Trac plugin for registering new HSI readers

class IHyperspectralFileFormat(Interface):
    def supportedFormats():
        """Return a list of classes that this plugin defines.
        """
    
class HyperspectralFileFormat(Component):
    handlers = ExtensionPoint(IHyperspectralFileFormat)

    loaded = False

    @staticmethod
    def discover():
        if HyperspectralFileFormat.loaded:
            return
        import ENVI
        try:
            import GDAL
        except Exception, e:
            dprint("GDAL not available")
        dprint("module name %s" % __name__)
        modbase, ext = os.path.splitext(__name__)
        
        path = os.path.dirname(__file__)
        files = glob.glob(os.path.join(path,'frmts','*.py'))
        files.extend(glob.glob(os.path.join(path,'mil','*.py')))
        for include in files:
            sub = os.path.basename(os.path.dirname(include))
            mod, ext = os.path.splitext(os.path.basename(include))
            mod = '%s.%s.%s' % (modbase, sub, mod)
            dprint('trying to load module %s' % mod)
            try:
                __import__(mod)
            except Exception,e:
                print e
        HyperspectralFileFormat.loaded = True

    @staticmethod
    def identifyall(urlinfo):
        HyperspectralFileFormat.discover()
        if not isinstance(urlinfo, URLInfo):
            urlinfo = URLInfo(urlinfo)
        comp_mgr = ComponentManager()
        register = HyperspectralFileFormat(comp_mgr)
        print "handlers: %s" % register.handlers
        matches = []
        for loader in register.handlers:
            for format in loader.supportedFormats():
                print "checking %s for %s format" % (urlinfo, format.format_name)
                if format.identify(urlinfo):
                    print "Possible match for %s format" % format.format_name
                    matches.append(format)
        order = []
        for match in matches:
            # It is possible that the file can be loaded as more than
            # one format.  For instance, GDAL supports a bunch of
            # formats, but custom classes can be written to be more
            # efficient than GDAL.  So, loop through the matches and
            # see if there is a specific class that should be used
            # instead of a generic one.
            print "Checking %s for specific support of %s" % (match, urlinfo)
            name, ext = os.path.splitext(urlinfo.path)
            ext.lower()
            if ext in match.extensions:
                print "Found specific support for %s in %s" % (ext, match)
                order.append(match)
                matches.remove(match)
        if len(matches)>0:
            order.extend(matches)
        return order

    @staticmethod
    def identify(urlinfo):
        matches = HyperspectralFileFormat.identifyall(urlinfo)
        if len(matches)>0:
            return matches[0]
        return None

    @staticmethod
    def load(urlinfo):
        HyperspectralFileFormat.discover()
        if not isinstance(urlinfo, URLInfo):
            urlinfo = URLInfo(urlinfo)
        matches = HyperspectralFileFormat.identifyall(urlinfo)
        for format in matches:
            dprint("Loading %s format cube" % format.format_name)
            h = format(urlinfo)
            try:
                cube=h.getCube()
                print cube
                return cube
            except Exception, e:
                dprint("Failed loading %s format cube" % format.format_name)
                dprint(e)
        return None

    def wildcards(self):
        pairs={}
        for loader in self.handlers:
            for format in loader.supportedFormats():
                pairs[format.format_name]=format.extensions
                
        names=pairs.keys()
        names.sort()
        wildcards=""
        for name in names:
            print "%s: %s" % (name,pairs[name])
            shown=';'.join("*"+ext for ext in pairs[name])
            expandedexts=list(pairs[name])
            expandedexts.extend(ext.upper() for ext in pairs[name])
            print expandedexts
            expanded=';'.join("*"+ext for ext in expandedexts)
            wildcards+="%s (%s)|%s|" % (name,shown,expanded)

        wildcards+="All files (*.*)|*.*"
        print wildcards
        return wildcards



class MetadataMixin(object):
    """Generic mixin interface for Cube metadata.

    This will be subclassed by various formats like ENVI and GDAL to
    load the metadata from files and to provide a method to load the
    cube data.
    """

    format_name="unknown"
    extensions=[]

    @classmethod
    def identify(cls, fh, filename=None):
        """Scan through the file-like object to identify if it is a
        valid instance of the type that the subclass is expecting to
        load."""
        return False

    def formatName(self):
        return self.format_name

    def fileExtensions(self):
        return self.extensions
    
    def open(self,filename=None):
        pass

    def save(self,filename=None):
        pass
    
    def getCube(self,filename=None,index=None):
        """Return a cube instance that represents the data pointed to
        by the metadata."""
        return None
    
    def __str__(self):
        fs=StringIO()
        order=self.keys()
        if self.debug: print "keys in object: %s" % order
        order.sort()
        for key in order:
            val=self[key]
            fs.write("%s = %s%s" % (key,val,os.linesep))
        return fs.getvalue()
    
class Cube(object):
    """Generic representation of an HSI datacube.  Specific subclasses
    L{BILCube}, L{BIPCube}, and L{BSQCube} exist to fill in the
    concrete implementations of the common formats of HSI data.
    """

    def __init__(self,filename=None):
        self.url = None
        self.setURL(filename)
        
        self.samples=-1
        self.lines=-1
        self.bands=-1
        self.interleave='unknown'
        self.sensor_type='unknown'

        # absolute pointer to data within the file
        self.file_offset=0
        # number of header bytes to skip when reading the raw data
        # file (relative to file_offset)
        self.header_offset=0
        # data_offset = cube_offset + header_offset.  This is an
        # absolute pointer to the raw data within the file
        self.data_offset=0
        self.data_bytes=0 # number of bytes in the data part of the file

        # Data type is a numarray data type, one of: [None,Int8,Int16,Int32,Float32,Float64,Complex32,Complex64,None,None,UInt16,UInt32,Int64,UInt64]
        self.data_type=None

        self.byte_order=nativeByteOrder
        self.swap=False

        # per band information, should be lists of dimension self.bands
        self.wavelengths=[]
        self.bbl=[]
        self.fwhm=[]
        self.band_names=[]

        # wavelength units: 'nm' for nanometers, 'um' for micrometers,
        # None for unknown
        self.wavelength_units=None

        # scale_factor is the value by which the samples in the cube
        # have already been multiplied.  To get values in the range of
        # 0.0 - 1.0, you must B{divide} by this value.
        self.scale_factor=None

        self.description=''

        # numarray internals
        self.mmap=None
        #self.header_data=None
        self.slice=None
        self.raw=None
        self.itemsize=0
        self.flat=None

        # calculated quantities
        self.spectraextrema=[None,None] # min and max over whole cube


    def __str__(self):
        s=StringIO()
        s.write("""Cube: filename=%s
        description=%s
        data_offset=%d header_offset=%d data_type=%s
        samples=%d lines=%d bands=%d data_bytes=%d
        interleave=%s byte_order=%d (native byte order=%d)\n""" % (self.url,self.description,self.data_offset,self.header_offset,str(self.data_type),self.samples,self.lines,self.bands,self.data_bytes,self.interleave,self.byte_order,nativeByteOrder))
        if self.scale_factor: s.write("        scale_factor=%f\n" % self.scale_factor)
        s.write("        wavelength units: %s\n" % self.wavelength_units)
        # s.write("        wavelengths: %s\n" % self.wavelengths)
        s.write("        bbl: %s\n" % self.bbl)
        # s.write("        fwhm: %s\n" % self.fwhm)
        # s.write("        band_names: %s\n" % self.band_names)
        s.write("        mmap=%s\n" % repr(self.mmap))
        s.write("        slice=%s\n" % repr(self.slice))
        return s.getvalue()

    def fileExists(self):
        return self.url.exists()
                
    def isDataLoaded(self):
        if isinstance(self.mmap,numpy.ndarray) or self.mmap or isinstance(self.raw,numpy.ndarray) or self.raw:
            return True
        return False

    def setURL(self, url=None):
        #print "setting url to %s" % url
        if url:
            if isinstance(url, str):
                url = URLInfo(url)
            self.url=url
        else:
            self.url=None

    def open(self,url=None):
        if url:
            self.setURL(url)

        if self.url:
            if self.url.protocol == 'file':
                if self.mmap is None: # don't try to reopen if already open
                    self.mmap=numpy.memmap(self.url.path,mode="r")
                    self.initialize()
            else:
                raise IOError("Only file protocols supported for mmap")
        else:
            raise IOError("No url specified.")
    
    def save(self,url=None):
        if url:
            self.setURL(url)

        if self.url:
            if self.mmap is not None and not isinstance(self.mmap,bool):
                self.mmap.flush()
                self.mmap.sync()
            else:
                self.raw.tofile(self.url)

    def initialize(self,datatype=None,byteorder=None):
        self.initializeSizes(datatype,byteorder)
        self.initializeMmap()
        self.initializeRaw()
        if self.raw!=None:
            self.shape()

        self.verifyAttributes()

    def initializeSizes(self,datatype=None,byteorder=None):
        if datatype:
            self.data_type=datatype
        if byteorder:
            self.byte_order=byteorder
        
        # find out how many bytes per element in this datatype
        if self.data_type:
            self.itemsize=numpy.empty([1],dtype=self.data_type).itemsize

        # calculate the size of the raw data only if it isn't already known
        if self.data_bytes==0:
            self.data_bytes=self.itemsize*self.samples*self.lines*self.bands

    def initializeMmap(self):
        if isinstance(self.mmap,numpy.ndarray):
            if self.data_offset>0:
                #self.header_data=self.mmap[self.file_offset:self.header_offset]
                if self.data_bytes>0:
                    self.slice=self.mmap[self.data_offset:self.data_offset+self.data_bytes]
                else:
                    self.slice=self.mmap[self.data_offset:]
            else:
                #self.header_data=None
                self.slice=self.mmap[:]
        elif self.mmap == True:
            return
                
    def initializeRaw(self):
        if self.raw==None:
            if isinstance(self.slice,numpy.ndarray):
                view=self.slice.view(self.data_type)
                #self.raw.reshape(len(self.slice)/self.itemsize),dtype=self.data_type,byteorder=byteordertext[self.byte_order])
                self.raw=view.newbyteorder(byteordertext[self.byte_order])

    def shape(self):
        """Shape the array based on the cube type"""
        pass

    def verifyAttributes(self):
        """Clean up after loading a cube to make sure some values are
        populated and that everything that should have defaults does."""

        # supply reasonable scale factor
        if self.scale_factor == None:
            self.guessScaleFactor()

        # supply bad band list
        if not self.bbl:
            self.bbl=[1]*self.bands
        # print "verifyAttributes: bands=%d bbl=%s" % (self.bands,self.bbl)

        # guess wavelength units if not supplied
        if self.wavelengths and not self.wavelength_units:
            self.guessWavelengthUnits()

        if self.byte_order != nativeByteOrder:
            #print "byteswapped data!"
            #self.swap=True

            # with numarray's byteorder parameter, we don't have to
            # actually perform any swapping by hand.
            pass

        if self.header_offset>0 or self.file_offset>0:
            if self.data_offset==0:
                # if it's not already set, set it
                self.data_offset=self.file_offset+self.header_offset

    

    def guessScaleFactor(self):
        """Try to supply a good guess as to the scale factor of the
        samples based on the type of the data"""
        if self.data_type in [int8,int16,int32,uint16,uint32,int64,uint64]:
            self.scale_factor=10000.0
        elif self.data_type in [float32,float64]:
            self.scale_factor=1.0
        else:
            self.scale_factor=1.0

    def guessDisplayBands(self):
        """Guess the best bands to display a false-color RGB image
        using the wavelength info from the cube's metadata."""
        if self.bands>=3 and self.wavelengths:
            # bands=[random.randint(0,self.bands-1) for i in range(3)]
            bands=[self.getBandListByWavelength(wl)[0] for wl in (660,550,440)]

            # If all the bands are the same, then visible light isn't
            # within the wavelength region
            if bands[0]==bands[1] and bands[1]==bands[2]:
                bands=[bands[0]]
        else:
            bands=[0]
        return bands
        
    def guessWavelengthUnits(self):
        """Try to guess the wavelength units if the wavelength list is
        supplied but the units aren't."""
        if self.wavelengths[-1]<100.0:
            self.wavelength_units='um'
        else:
            self.wavelength_units='nm'

    def updateExtrema(self, spectra):
        mn=spectra.min()
        if self.spectraextrema[0]==None or mn<self.spectraextrema[0]:
            self.spectraextrema[0]=mn
        mx=spectra.max()
        if self.spectraextrema[1]==None or  mx>self.spectraextrema[1]:
            self.spectraextrema[1]=mx

    def getUpdatedExtrema(self):
        return self.spectraextrema

    def getPixel(self,line,sample,band):
        """Get an individual pixel at the specified line, sample, & band"""
        pass

    def getBand(self,band):
        """Get a copy of the array of (lines x samples) at the
        specified band.  You are not working on the original data."""
        s=self.getBandInPlace(band).copy()
        if self.swap:
            s.byteswap(True)
        return s

    def getBandInPlace(self,band):
        """Get the slice of the data array (lines x samples) at the
        specified band.  This points to the actual in-memory array."""
        s=self.getBandRaw(band)
        self.updateExtrema(s)
        return s

    def getBandRaw(self,band):
        pass

    def getSpectra(self,line,sample):
        """Get the spectra at the given pixel.  Calculate the extrema
        as we go along."""
        spectra=self.getSpectraInPlace(line,sample).copy()
        if self.swap:
            spectra.byteswap()
        spectra*=self.bbl
        self.updateExtrema(spectra)
        return spectra
        
    def getSpectraInPlace(self,line,sample):
        """Get the spectra at the given pixel.  Calculate the extrema
        as we go along."""
        spectra=self.getSpectraRaw(line,sample)
        return spectra

    def getSpectraRaw(self,line,sample):
        """Get the spectra at the given pixel"""
        pass

    def getLineOfSpectra(self,line):
        """Get the all the spectra along the given line.  Calculate
        the extrema as we go along."""
        spectra=self.getLineOfSpectraCopy(line)
        if self.swap:
            spectra.byteswap()
        spectra*=self.bbl
        self.updateExtrema(spectra)
        return spectra
        
    def getLineOfSpectraCopy(self,line):
        """Get the spectra along the given line.  Subclasses override
        this."""
        raise NotImplementedError

    def normalizeUnits(self,val,units):
        if not self.wavelength_units:
            return val
        cubeunits=utils.units_scale[self.wavelength_units]
        theseunits=utils.units_scale[units]
##        converted=val*cubeunits/theseunits
        converted=val*theseunits/cubeunits
        #print "val=%s converted=%s cubeunits=%s theseunits=%s" % (str(val),str(converted),str(cubeunits),str(theseunits))
        return converted

    def normalizeUnitsTo(self,val,units):
        """Normalize wavelength units from the cube's default to the
        specified unit.
        """
        if not self.wavelength_units:
            return val
        cubeunits=utils.units_scale[self.wavelength_units]
        theseunits=utils.units_scale[units]
        converted=val*cubeunits/theseunits
        #print "val=%s converted=%s cubeunits=%s theseunits=%s" % (str(val),str(converted),str(cubeunits),str(theseunits))
        return converted

    def getBandListByWavelength(self,wavelen_min,wavelen_max=-1,units='nm'):
        """Get list of bands between the specified wavelength, or if
        the wavelength range is too small, get the nearest band."""
        bandlist=[]
        if wavelen_max<0:
            wavelen_max=wavelen_min
        wavelen_min=self.normalizeUnits(wavelen_min,units)
        wavelen_max=self.normalizeUnits(wavelen_max,units)
        if not self.wavelengths:
            return bandlist
        
        for channel in range(self.bands):
            # print "wavelen[%d]=%f" % (channel,self.wavelengths[channel])
            if (self.bbl[channel]==1 and
                  self.wavelengths[channel]>=wavelen_min and
                  self.wavelengths[channel]<=wavelen_max):
                bandlist.append(channel)
        if not bandlist:
            center=(wavelen_max+wavelen_min)/2.0
            if center<self.wavelengths[0]:
                for channel in range(self.bands):
                    if self.bbl[channel]==1:
                        bandlist.append(channel)
                        break
            elif center>self.wavelengths[self.bands-1]:
                for channel in range(self.bands-1,-1,-1):
                    if self.bbl[channel]==1:
                        bandlist.append(channel)
                        break
            else:
                for channel in range(self.bands-1):
                    if (self.bbl[channel]==1 and
                           self.wavelengths[channel]<center and
                           self.wavelengths[channel+1]>center):
                        if (center-self.wavelengths[channel] <
                               self.wavelengths[channel+1]-center):
                            bandlist.append(channel)
                            break
                        else:
                            bandlist.append(channel+1)
                            break
        return bandlist

    def getFlatView(self):
        """Get a flat, one-dimensional view of the data"""
        #self.flat=self.raw.view()
        #self.flat.setshape((self.raw.size()))
        return self.raw.flat

    def getBandBoundary(self):
        """return the number of items you have to add to a flat
        version of the data until you reach the next band in the data"""
        pass

    def flatToLocation(self,pos):
        """Convert the flat index to a tuple of line,sample,band"""
        pass

    def locationToFlat(self,line,sample,band):
        """Convert location (line,sample,band) to flat index"""
        pass
    
    def getBadBandList(self,other=None):
        if other:
            bbl2=[0]*self.bands
            for i in range(self.bands):
                if self.bbl[i] and other.bbl[i]:
                    bbl2[i]=1
            return bbl2
        else:
            return self.bbl
        


# BIP: (line,sample,band)
class BIPCube(Cube):
    def __init__(self,url=None):
        Cube.__init__(self,url)
        self.interleave='bip'
        
    def shape(self):
        self.raw=numpy.reshape(self.raw,(self.lines,self.samples,self.bands))

    def getPixel(self,line,sample,band):
        return self.raw[line][sample][band]

    def getBandRaw(self,band):
        """Get an array of (lines x samples) at the specified band"""
        s=self.raw[:,:,band]
        return s

    def getSpectraRaw(self,line,sample):
        """Get the spectra at the given pixel"""
        s=self.raw[line,sample,:]
        return s

    def getLineOfSpectraCopy(self,line):
        """Get the spectra along the given line"""
        s=self.raw[line,:,:].copy()
        return s

    def getBandBoundary(self):
        return 1

    def flatToLocation(self,pos):
        line=pos/(self.bands*self.samples)
        temp=pos%(self.bands*self.samples)
        sample=temp/self.bands
        band=temp%self.bands
        return (line,sample,band)

    def locationToFlat(self,line,sample,band):
        pos=line*self.bands*self.samples + sample*self.bands + band
        return pos

    
class BILCube(Cube):
    def __init__(self,url=None):
        Cube.__init__(self,url)
        self.interleave='bil'
        
    def shape(self):
        self.raw=numpy.reshape(self.raw,(self.lines,self.bands,self.samples))

    def getPixel(self,line,sample,band):
        return self.raw[line][band][sample]

    def getBandRaw(self,band):
        """Get an array of (lines x samples) at the specified band"""
        s=self.raw[:,band,:]
        return s

    def getSpectraRaw(self,line,sample):
        """Get the spectra at the given pixel"""
        s=self.raw[line,:,sample]
        return s
    
    def getLineOfSpectraCopy(self,line):
        """Get the spectra along the given line"""
        s=numpy.transpose(self.raw[line,:,:])
        return s
    
    def getBandBoundary(self):
        return self.samples

    def flatToLocation(self,pos):
        line=pos/(self.bands*self.samples)
        temp=pos%(self.bands*self.samples)
        band=temp/self.samples
        sample=temp%self.samples
        return (line,sample,band)

    def locationToFlat(self,line,sample,band):
        pos=line*self.bands*self.samples + band*self.samples + sample
        return pos



class BSQCube(Cube):
    def __init__(self,url=None):
        Cube.__init__(self,url)
        self.interleave='bsq'
        
    def shape(self):
        self.raw=numpy.reshape(self.raw,(self.bands,self.lines,self.samples))

    def getPixel(self,line,sample,band):
        return self.raw[band][line][sample]

    def getBandRaw(self,band):
        """Get an array of (lines x samples) at the specified band"""
        s=self.raw[band,:,:]
        return s

    def getSpectraRaw(self,line,sample):
        """Get the spectra at the given pixel"""
        s=self.raw[:,line,sample]
        return s

    def getLineOfSpectraCopy(self,line):
        """Get the spectra along the given line"""
        s=numpy.transpose(self.raw[:,line,:])
        return s
    
    def getBandBoundary(self):
        return self.samples*self.lines

    def flatToLocation(self,pos):
        band=pos/(self.lines*self.samples)
        temp=pos%(self.lines*self.samples)
        line=temp/self.samples
        sample=temp%self.samples
        return (line,sample,band)

    def locationToFlat(self,line,sample,band):
        pos=band*self.lines*self.samples + line*self.samples + sample
        return pos

    

def newCube(interleave,url=None):
    i=interleave.lower()
    if i=='bil':
        cube=BILCube(url)
    elif i=='bip':
        cube=BIPCube(url)
    elif i=='bsq':
        cube=BSQCube(url)
##    elif i=='gdal':
##        cube=GDALCube(url)
    else:
        raise ValueError("Interleave format %s not supported." % interleave)
    return cube

def createCube(interleave,lines,samples,bands,datatype=int16,byteorder=nativeByteOrder,scalefactor=10000.0):
    cube=newCube(interleave,None)
    cube.interleave=interleave
    cube.samples=samples
    cube.lines=lines
    cube.bands=bands
    cube.data_type=datatype
    cube.byte_order=byteorder
    cube.scale_factor=scalefactor
    cube.raw=numpy.zeros((samples*lines*bands),dtype=datatype)
    cube.mmap=True
    cube.initialize(datatype,byteorder)
    return cube


if __name__ == "__main__":
    c=BIPCube()
    c.samples=5
    c.lines=4
    c.bands=3
    c.raw=array(arange(c.samples*c.lines*c.bands))
    c.shape()
    print c.raw
    print c.getPixel(0,0,0)
    print c.getPixel(0,0,1)
    print c.getPixel(0,0,2)