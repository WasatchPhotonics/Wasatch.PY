################################################################################
#                                                                              #
#                                   utils.py                                   #
#                                                                              #
################################################################################

import numpy

def generate_wavelengths(pixels, c0, c1, c2, c3):
    wavelengths = []
    for x in range(pixels):
        wavelength = c0           \
                   + c1 * x       \
                   + c2 * x * x   \
                   + c3 * x * x * x
        wavelengths.append(wavelength)
    return wavelengths            

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

# http://stackoverflow.com/questions/14313510/how-to-calculate-moving-average-using-numpy
# NOTE: this trims the ends of the array!
def moving_average(a, n):
    ret = numpy.cumsum(a, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    return ret[n - 1:] / n 

def apply_boxcar(a, half_width):
    if a is None:
        return None
    if half_width < 1:
        return a
    return numpy.hstack((a[0:half_width], 
                         moving_average(a, half_width * 2 + 1), 
                         a[-half_width:])).ravel()

def dump(foo, indent=0):
    spc  = '  ' * indent
    spc1 = '  ' * (indent + 1)
    s = ""

    if isinstance(foo, dict):
        s += spc + '{\n'
        for key,val in foo.iteritems():
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
