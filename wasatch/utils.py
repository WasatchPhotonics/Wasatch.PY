# ##############################################################################
#                                                                              #
#                                   utils.py                                   #
#                                                                              #
# ##############################################################################

import datetime
import logging
import numpy
import json
import os
import re

log = logging.getLogger(__name__)

## convert unicode string to ascii
def remove_unicode(s):
    if isinstance(s, str):
        return s.encode('ascii', 'ignore')
    return s

## expand 3rd-order wavelength polynomial into array of wavelengths
def generate_wavelengths(pixels, c0, c1, c2, c3):
    wavelengths = []
    for x in range(pixels):
        wavelength = c0           \
                   + c1 * x       \
                   + c2 * x * x   \
                   + c3 * x * x * x
        wavelengths.append(wavelength)
    return wavelengths            

## convert wavelengths into Raman shifts in 1/cm wavenumbers from the given 
#  excitation wavelength
def generate_wavenumbers(excitation, wavelengths):
    wavenumbers = []
    if not wavelengths or excitation < 1:
        return wavenumbers

    base = 1e7 / float(excitation)
    for i in range(len(wavelengths)):
        if wavelengths[i] != 0:
            wavenumbers.append(base - 1e7 / wavelengths[i])
        else:
            wavenumbers.append(0)
    return wavenumbers

##
# compute a moving average on array 'a' of width 'n'
#
# @note this trims the ends of the array!  len(a) > len(moving_average(a, n))
# @see http://stackoverflow.com/questions/14313510/how-to-calculate-moving-average-using-numpy
def moving_average(a, n):
    ret = numpy.cumsum(a, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    return ret[n - 1:] / n 

##
# apply a boxcar convolution of the given half_width to input array 'a'
def apply_boxcar(a, half_width):
    if a is None:
        return None
    if half_width < 1:
        return a
    
    # "horizontally stack" a series of lists, then flatten them sequentially
    return numpy.hstack((a[0:half_width], 
                         moving_average(a, half_width * 2 + 1), 
                         a[-half_width:])).ravel()

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
def update_obj_from_dict(dest_obj, src_dict):
    for k in sorted(dest_obj.__dict__.keys()):
        if k in src_dict:
            log.debug("%s -> %s", k, src_dict[k])
            setattr(dest_obj, k, src_dict[k])

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
        return null
    return numpy.interp(new_axis, old_axis, spectrum)

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
    elif hasattr(flag, "__len__"): # lists, arrays, Numpy
        return flag.__len__ > 0
    else:
        return flag
