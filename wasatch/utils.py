# ##############################################################################
#                                                                              #
#                                   utils.py                                   #
#                                                                              #
# ##############################################################################

import datetime
import logging
import ctypes
import numpy
import json
import math
import os
import re

log = logging.getLogger(__name__)

# see https://stackoverflow.com/questions/1026431/cross-platform-way-to-check-admin-rights-in-a-python-script-under-windows
def check_admin():
    try:
        is_admin = os.getuid() == 0
    except AttributeError:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    return is_admin

## convert unicode string to ascii
def remove_unicode(s):
    if isinstance(s, str):
        return s.encode('ascii', 'ignore')
    return s

def pixel_to_wavelength(x: int, coeffs: list[float]): # -> float 
    wavelength = 0.0
    log.debug(f"converting pixel {x} to wavelen with coeffs {coeffs}")
    for i in range(len(coeffs)):
        wavelength += coeffs[i] * pow(x, i)
    return wavelength

## expand 3rd-order wavelength polynomial into array of wavelengths
def generate_wavelengths(pixels, coeffs):
    if coeffs is None or pixels == 0:
        return None

    wavelengths = []
    for x in range(pixels):
        wavelength = 0.0
        for i in range(len(coeffs)):
            wavelength += coeffs[i] * pow(x, i)
        wavelengths.append(wavelength)
    return wavelengths            

def generate_wavelengths_from_wavenumbers(excitation, wavenumbers):
    return [1.0 / ((1.0 / excitation) - (wavenumber * 1e-7)) for wavenumber in wavenumbers]

## convert wavelengths into Raman shifts in 1/cm wavenumbers from the given 
#  excitation wavelength
def generate_wavenumbers(excitation, wavelengths, wavenumber_correction=0):
    wavenumbers = []
    if not wavelengths or excitation < 1:
        return wavenumbers

    base = 1e7 / float(excitation)
    for i in range(len(wavelengths)):
        wavenumber = 0
        if wavelengths[i] != 0:
            wavenumber = base - 1e7 / wavelengths[i]
        wavenumbers.append(wavenumber + wavenumber_correction)
    return wavenumbers

## convert a single wavelength to wavenumber
def wavelength_to_wavenumber(wavelength, excitation):
    return 1e7 / float(excitation) - 1e7 / wavelength

## convert a single (uncorrected) wavenumber to wavelength
def wavenumber_to_wavelength(excitation, wavenumber):
    return 1.0 / ((1.0 / excitation) - (wavenumber * 1e-7)) 

##
# If we've loaded a CSV that had wavelength and wavenumber columns, but no
# metadata, use this to infer the excitation wavelength.  Useful for 
# interpolation.
def generate_excitation(wavelengths, wavenumbers):
    if wavelengths is None or wavenumbers is None or len(wavelengths) != len(wavenumbers) or len(wavelengths) < 1:
        return None

    total = 0.0
    count = len(wavelengths)
    for i in range(count):
        excitation = 1e7 / (wavenumbers[i] + 1e7/wavelength[i])
        total += excitation
    return total / count

##
# apply a boxcar convolution of the given half_width to input array 'a'
def apply_boxcar(a, half_width):
    out = []
    for i in range(len(a)):
        # hw is smaller than half_width near the fringes
        hw = min(i, half_width, len(a)-1-i)
        # each pixel is the mean of itself and `hw` pixels to the left and right
        out.append(sum(a[i-hw:i+hw+1]) / (2*hw+1))
    return out

## similar to Perl's Data::Dumper
def dump(foo, indent=0):
    spc  = '  ' * indent
    spc1 = '  ' * (indent + 1)
    s = ""

    if isinstance(foo, dict):
        s += spc + '{\n'
        for key,val in foo.items():
            if isinstance(val, (dict, list, tuple)):
                s += spc1 + str(key) + '=>\n'
                s += dump(val, indent+2)
            else:
                s += spc1 + str(key) + '=> ' + str(val)
        s += spc + '}\n'

    elif isinstance(foo, list):
        s += spc + '[\n'
        for item in foo:
            s += dump(item, indent+1)
        s += spc + ']\n'

    elif isinstance(foo, tuple):
        s += spc + '(\n'
        for item in foo:
            s += dump(item, indent+1)
        s += spc + ')\n'

    else: 
        s += spc + str(foo)

    return s

## given a destination object and a source dictionary, update any existing 
#  attributes of the destination object from like-named keys in the source 
#  dictionary
#
# @param obj (In/Out) the object whose attributes to update
# @param d   (Input)  the dictionary whose keys should be treated as attributes
def update_obj_from_dict(obj, d):
    if obj is None or d is None:
        return
    for k in sorted(obj.__dict__.keys()):
        v = dict_get_norm(d, k)
        if v is not None:
            log.debug("%s -> %s", k, v)
            setattr(obj, k, v)

##
# Similar to dict.get(), but case-insensitive and normalizes-out spaces, 
# underscores, periods and hyphens.
#
# @param d (input) dictionary
# @param k (input) case-insensitive key (can be prioritized list)
#
# Note that this function does not distiguish between the dictionary not having
# a key, and the value of the key being None.
def dict_get_norm(d, keys):

    # if we weren't passed a list, make it one
    if not isinstance(keys, list):
        keys = [ keys ]

    try:
        pat = r"[ ._-]"
        for key in keys:
            key = re.sub(pat, "", key).lower()
            for k, v in d.items():
                k = re.sub(pat, "", k).lower()
                if k == key:
                    return v
    except:
        log.error("dict_get_norm: %s", keys, exc_info=1)
        return

##
# Similar to dict.get(), but takes a list of keys to be traversed in sequence.
#
# @param d    (input) dictionary
# @param keys (input) list of case-insensitive keys
def dict_get_path(d, keys):
    try:
        while len(keys) > 0:
            k = keys.pop(0)
            v = dict_get_norm(d, k)
            if v is None:
                return
            elif len(keys) == 0:
                return v
            else:
                d = v
    except:
        return

## convenience wrapper to load a JSON file
def load_json(pathname):
    try:
        with open(pathname) as infile:
            return json.load(infile)
    except:
        log.error("unable to load %s", pathname, exc_info=1)

## iterate down a directory, returning pathnames that match the given pattern
def get_pathnames_from_directory(rootdir, pattern=None, recursive=False):
    pathnames = []
    # log.debug("searching %s matching %s with recursive %s", rootdir, pattern, recursive)
    if recursive:
        for (directory, dirnames, filenames) in walk(rootdir):
            for filename in filenames:
                pathname = os.path.join(directory, filename)
                if pattern:
                    if re.search(pattern, filename):
                        pathnames.append(pathname)
                    else:
                        # log.debug("%s does not match %s", pathname, pattern)
                        pass
                else:
                    pathnames.append(pathname)
    elif os.path.isdir(rootdir):
        for filename in os.listdir(rootdir):
            pathname = os.path.join(rootdir, filename)
            if os.path.isfile(pathname):
                if pattern:
                    if re.search(pattern, filename):
                        pathnames.append(pathname)
                    else:
                        # log.debug("%s does not match %s", pathname, pattern)
                        pass
                else:
                    pathnames.append(pathname)
    # log.debug("returning %s", pathnames)
    return pathnames

##
# Given a spectrum (array 'a'), with an x_axis, a 'center' along that x_axis, and
# an allowed 'tolerance' (in same units as the x_axis), find the local maxima
# within 'tolerance' of 'center'.
#
# @note probably a numpy shortcut for this
def find_local_maxima(a, x_axis, center, tolerance=0):
    # log.debug("find_local_maxima: center %.2f (tolerance %.2f)", center, tolerance)
    # generate subset of array within tolerance of center
    x = []
    y = []
    indices = []
    for i in range(len(x_axis)):
        x_value = x_axis[i]
        if center - tolerance <= x_value <= center + tolerance:
            indices.append(i)
            x.append(x_value)
            y.append(a[i])

    # log.debug("  range x: %s", x)
    # log.debug("  range y: %s", y)
    
    if not x:
        raise "no points within %s of %s"

    # find maxima within subset
    best_x_index = indices[0]
    best_x_value = x_axis[0]
    best_y_value = y[0]
    for i in range(len(x)):
        if best_y_value < y[i]:
            best_x_index = indices[i]
            best_x_value = x_axis[best_x_index]
            best_y_value = y[i]

    # no point with linear interpolation, as it would only go "down"
    # (could do Gaussian / polynomial fit)

    # log.debug("  best_x_index: %d", best_x_index)
    # log.debug("  best_x_value: %.2f", best_x_value)
    # log.debug("  best_y_value: %.2f", best_y_value)

    return (best_y_value, best_x_value, best_x_index)

##
# Given a spectrum and an x_axis, find the indexes of the left and right
# 'feet' of the peak centered on x_index.  Internally apply then given boxcar
# for added smoothing.
def find_peak_feet_indices(spectrum, x_axis, x_index, boxcar_half_width=0):
    if boxcar_half_width:
        smoothed = apply_boxcar(spectrum, boxcar_half_width)
    else:
        smoothed = spectrum

    left_index = x_index
    for i in range(x_index - (boxcar_half_width + 1), -1, -1):
        if i == 0 or smoothed[i] > smoothed[left_index]:
            break
        left_index = i

    right_index = x_index
    for i in range(x_index + (boxcar_half_width + 1), len(spectrum)):
        if i + 1 == len(spectrum) or smoothed[i] > smoothed[right_index]:
            break
        right_index = i

    return (left_index, right_index)

## integrate the 'area under the curve' for the given spectrum and x_axis,
#  using the peak centered on x_axis, with optional smoothing given the
#  boxcar width.
def area_under_peak(spectrum, x_axis, x_index, boxcar_half_width=0):
    # find left and right "feet" of the peak
    (left_index, right_index) = find_peak_feet_indices(
        spectrum, x_axis, x_index, boxcar_half_width)

    # generate baseline-subtracted subspectrum of just the peak, considering
    #    the baseline to be a straight line between the two feet
    slope = float(spectrum[right_index] - spectrum[left_index]) / \
                   (x_axis[right_index] - x_axis[left_index])
    subspectrum = []
    subaxis = []
    for i in range (left_index, right_index + 1):
        baseline = spectrum[left_index] + slope * (x_axis[i] - x_axis[left_index])
        subspectrum.append(spectrum[i] - baseline)
        subaxis.append(x_axis[i])

    # 4. integrate subspectrum
    area = numpy.trapz(subspectrum, subaxis)
    return area

def peak_height_above_background(spectrum, x_axis, x_index, boxcar_half_width=0):
    # find left and right "feet" of the peak
    (left_index, right_index) = find_peak_feet_indices(
        spectrum, x_axis, x_index, boxcar_half_width)

    width_wn = x_axis[right_index] - x_axis[left_index]
    width_px = right_index - left_index + 1

    # generate baseline-subtracted subspectrum of just the peak, considering
    #    the baseline to be a straight line between the two feet
    slope = float(spectrum[right_index] - spectrum[left_index]) / width_wn
    baseline = spectrum[left_index] + slope * (x_axis[x_index] - x_axis[left_index])
    height = spectrum[x_index] - baseline

    log.debug("peak_height_above_background: peak at x_index %d (boxcar %d)", x_index, boxcar_half_width)
    log.debug("peak_height_above_background:   abs height: %.2f", spectrum[x_index])
    log.debug("peak_height_above_background:   peak width: (%d px, %.2f cm-1)", width_px, width_wn)
    log.debug("peak_height_above_background:   feet: (%d, %d)", left_index, right_index)
    log.debug("peak_height_above_background:   feet height: (%.2f, %.2f)", spectrum[left_index], spectrum[right_index])
    log.debug("peak_height_above_background:   slope: %.2f", slope)
    log.debug("peak_height_above_background:   peak baseline: %.2f", baseline)
    log.debug("peak_height_above_background:   relative height: %.2f", height)

    return (height, width_wn, width_px)

def find_nearest_index(L, value):
    a = numpy.asarray(L)
    return (numpy.abs(a - value)).argmin()
    
def find_nearest_value(L, value):
    i = find_nearest_index(L, value)
    return L[i]

## 
# Interpolate the passed spectrum over a fixed x-axis (e.g. integral wavelengths
# or wavenumbers).
def interpolate_array(spectrum, old_axis, new_axis):
    if not spectrum or not old_axis or not new_axis or len(spectrum) != len(old_axis) or len(new_axis) < 1:
        return 
    return numpy.interp(new_axis, old_axis, spectrum)

## I might be making this more difficult than it needs to be
def interpolate_value(spectrum, old_axis, x):
    if not spectrum or not old_axis or not new_axis or len(spectrum) != len(old_axis) or len(new_axis) < 1:
        return 
    new_axis = [ x-1, x, x+1 ] 
    new_y = numpy.interp(new_axis, old_axis, spectrum)
    if new_y is not None and len(new_y) == len(new_axis):
        return new_y[1]

## render a spectrum as ASCII-art
def ascii_spectrum(spectrum, rows, cols, x_axis, x_unit):
    spectral_min = min(spectrum)
    spectral_max = max(spectrum)
    spectral_avg = 1.0 * sum(spectrum) / len(spectrum)

    # histogram into bins
    bins = [0] * cols
    for i in range(len(spectrum)):
        col = int(1.0 * cols * i / len(spectrum))
        bins[col] += spectrum[i] - spectral_min

    # render histogram
    lines = []
    bin_hi = max(bins)
    for row in range(rows - 1, -1, -1):
        s = "| "
        for col in range(cols):
            s += "*" if bins[col] >= (1.0 * row / rows) * bin_hi else " "
        lines.append(s)

    # graph footer
    lines.append("+-" + "-" * cols)
    lines.append("  Min: %8.2f  Max: %8.2f  Mean: %8.2f  (range %.2f, %.2f%s)" % (
        spectral_min, spectral_max, spectral_avg, 
        x_axis[0], x_axis[-1], x_unit))

    return lines

def timestamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

def truthy(flag):
    if flag is None:
        return False

    try:
        if len(flag) > 0:
            return True
    except:
        pass

    return True if flag else False

def clean_nan(a):
    for i in range(len(a)):
        if math.isnan(a[i]):
            a[i] = 0

## 
# Can be used as a sanity-check for any set of coefficients.
#
# Checks that coeffs:
#
# - are not None
# - have no NaN
# - are not all the same (zeros, -1 etc)
# - are not [0, 1, 0, 0]
# - checks count if provided
def coeffs_look_valid(coeffs, count=None):

    if coeffs is None:
        log.debug("no coeffs, returning False")
        return False

    if count is not None and len(coeffs) != count:
        log.debug("coeff count is wrong, returning False")
        return False

    # check for NaN
    for i in range(len(coeffs)):
        if math.isnan(coeffs[i]):
            log.debug("found NaN in coeff, returning False")
            return False 

    # check for [0, 1, 0...] default pattern
    all_default = True
    for i in range(len(coeffs)):
        if i == 1:
            if coeffs[i] != 1.0:
                all_default = False
        elif coeffs[i] != 0.0:
            all_default = False
    if all_default:
        log.debug("coeffs all default, returning False")
        return False

    # check for constants (all coefficients the same value)
    all_const = True
    log.info(coeffs)
    for i in range(1, len(coeffs)):
        if coeffs[0] != coeffs[i]:
            all_const = False
    if all_const:
        log.debug("coeffs all const, returning False")
        return False

    return True

## 
# "Stomps" the first "count" elements with the first non-stomped value.
#
# @param a     (Input) array to modify
# @param count (Input) HOW MANY leading elements to stomp, so the index of the
#              first GOOD pixel should be one more than this
def stomp_first(a, count):
    for i in range(count):
        a[i] = a[count]

## "stomps" the last "count" elements with the last non-stomped value
def stomp_last(a, count):
    for i in range(count):
        a[-(i+1)] = a[-(count+1)]

def clamp_to_int16(n):
    return max(-32768, min(32767, int(n)))

##
# Given an array of doubles and a peak index, use the peak and its two
# neighbors to form a parabola and return the interpolated maximum height of the
# parabola.
#
# "pixel" is ideally the array index of the pinnacle of a previously-
# identified peak within the spectrum, although though this will 
# technically generate a parabola through any pixel and its two 
# neighbors.
#
# @param pixel  index of a point on the spectrum
# @param x      x-axis (wavelengths or wavenumbers)
# @param y      y-axis (intensity)
#
# @see https://stackoverflow.com/a/717833
#
# @returns a point representing the interpolated vertex of a parabola drawn 
#          through the specified pixel and its two neighbors (in x-axis space)
#
def parabolic_approximation(pixel, x, y):
    if len(x) != len(y):
        log.error("parabolic approximation array lengths differ")
        return 0, 0
    if pixel - 1 < 0:
        return y[0]
    elif pixel + 1 >= len(y):
        return y[-1]

    x1 = pixel - 1
    x2 = pixel
    x3 = pixel + 1

    y1 = y[x1]
    y2 = y[x2]
    y3 = y[x3]

    if y1 >= y2 or y3 >= y2:
        log.debug("parabolic approximation: peak misformed or saturated")

    denom = (x1 - x2) * (x1 - x3) * (x2 - x3)
    A = (x3 * (y2 - y1) + x2 * (y1 - y3) + x1 * (y3 - y2)) / denom
    B = (x3 * x3 * (y1 - y2) + x2 * x2 * (y3 - y1) + x1 * x1 * (y2 - y3)) / denom
    C = (x2 * x3 * (x2 - x3) * y1 + x3 * x1 * (x3 - x1) * y2 + x1 * x2 * (x1 - x2) * y3) / denom

    vertex_x = -B / (2 * A) # pixel space
    vertex_y = C - B * B / (4 * A)

    if vertex_x < x1 or vertex_x > x3:
        log.error("parabolic approximation failed (x exceeded limits)")
        return (0, 0)

    if vertex_x == x2:
        return (x[x2], vertex_y)
    elif vertex_x < x2:
        left = x1
        right = x2
    else:
        left = x2
        right = x3
    x_coord = x[left] + (x[right] - x[left]) * (vertex_x - left)

    log.debug("parabolic approximation: x1 %d, x2 %d, x3 %d", x1, x2, x3)
    log.debug("parabolic approximation: x.x1 %.2f, x.x2 %.2f, x.x3 %.2f", x[x1], x[x2], x[x3])
    log.debug("parabolic approximation: y.x1 %.2f, y.x2 %.2f, y.x3 %.2f", y[x1], y[x2], y[x3])
    log.debug("parabolic approximation: vertex_x %.2f, vertex_y %.2f", vertex_x, vertex_y)
    log.debug("parabolic approximation: left %d, right %d", left, right)
    log.debug("parabolic approximation: x.left %.2f, x.right %.2f", x[left], x[right])
    log.debug("parabolic approximation: x.coord %.2f", x_coord)

    return (x_coord, vertex_y)

##
# @see https://stackoverflow.com/a/9147327/6436775
def twos_complement(val, bits):
    if (val & (1 << (bits - 1))) != 0: # if sign bit is set e.g., 8bit: 128-255
        val = val - (1 << bits)        # compute negative value
    return val    

def to_bool(value):
    if isinstance(value, bool):
        return value
    elif isinstance(value, int):
        return 0 != value
    elif isinstance(value, float):
        return 0 != value
    elif isinstance(value, str):
        s = value.lower().strip()
        return s in ['true', 'y', 'yes', 'on', '1']
    return False

def uint16_to_little_endian(values):
    a = []
    for n in values:
        a.append(n & 0xff)          # lsb
        a.append((n >> 8) & 0xff)   # msb
    return a
