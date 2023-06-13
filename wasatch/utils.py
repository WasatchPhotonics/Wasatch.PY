import datetime
import logging
import ctypes
import numpy
import json
import math
import os
import re
log = logging.getLogger(__name__)


def check_admin():
    try:
        is_admin = os.getuid() == 0
    except AttributeError:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    return is_admin


def remove_unicode(s):
    if isinstance(s, str):
        return s.encode('ascii', 'ignore')
    return s


def pixel_to_wavelength(x, coeffs):
    wavelength = 0.0
    log.debug(f'converting pixel {x} to wavelen with coeffs {coeffs}')
    for i in range(len(coeffs)):
        wavelength += coeffs[i] * pow(x, i)
    return wavelength


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
    return [(1.0 / (1.0 / excitation - wavenumber * 1e-07)) for wavenumber in
        wavenumbers]


def generate_wavenumbers(excitation, wavelengths, wavenumber_correction=0):
    wavenumbers = []
    if not wavelengths or excitation < 1:
        return wavenumbers
    base = 10000000.0 / float(excitation)
    for i in range(len(wavelengths)):
        wavenumber = 0
        if wavelengths[i] != 0:
            wavenumber = base - 10000000.0 / wavelengths[i]
        wavenumbers.append(wavenumber + wavenumber_correction)
    return wavenumbers


def wavelength_to_wavenumber(wavelength, excitation):
    return 10000000.0 / float(excitation) - 10000000.0 / wavelength


def wavenumber_to_wavelength(excitation, wavenumber):
    return 1.0 / (1.0 / excitation - wavenumber * 1e-07)


def generate_excitation(wavelengths, wavenumbers):
    if wavelengths is None or wavenumbers is None or len(wavelengths) != len(
        wavenumbers) or len(wavelengths) < 1:
        return None
    total = 0.0
    count = len(wavelengths)
    for i in range(count):
        excitation = 10000000.0 / (wavenumbers[i] + 10000000.0 / wavelength[i])
        total += excitation
    return total / count


def moving_average(a, n):
    ret = numpy.cumsum(a, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    return ret[n - 1:] / n


def apply_boxcar(a, half_width):
    if a is None:
        return None
    if half_width < 1:
        return a
    return numpy.hstack((a[0:half_width], moving_average(a, half_width * 2 +
        1), a[-half_width:])).ravel()


def dump(foo, indent=0):
    spc = '  ' * indent
    spc1 = '  ' * (indent + 1)
    s = ''
    if isinstance(foo, dict):
        s += spc + '{\n'
        for key, val in foo.items():
            if isinstance(val, (dict, list, tuple)):
                s += spc1 + str(key) + '=>\n'
                s += dump(val, indent + 2)
            else:
                s += spc1 + str(key) + '=> ' + str(val)
        s += spc + '}\n'
    elif isinstance(foo, list):
        s += spc + '[\n'
        for item in foo:
            s += dump(item, indent + 1)
        s += spc + ']\n'
    elif isinstance(foo, tuple):
        s += spc + '(\n'
        for item in foo:
            s += dump(item, indent + 1)
        s += spc + ')\n'
    else:
        s += spc + str(foo)
    return s


def update_obj_from_dict(obj, d):
    if obj is None or d is None:
        return
    for k in sorted(obj.__dict__.keys()):
        v = dict_get_norm(d, k)
        if v is not None:
            log.debug('%s -> %s', k, v)
            setattr(obj, k, v)


def dict_get_norm(d, keys):
    if not isinstance(keys, list):
        keys = [keys]
    try:
        pat = '[ ._-]'
        for key in keys:
            key = re.sub(pat, '', key).lower()
            for k, v in d.items():
                k = re.sub(pat, '', k).lower()
                if k == key:
                    return v
    except:
        log.error('dict_get_norm: %s', keys, exc_info=1)
        return


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


def load_json(pathname):
    try:
        with open(pathname) as infile:
            return json.load(infile)
    except:
        log.error('unable to load %s', pathname, exc_info=1)


def get_pathnames_from_directory(rootdir, pattern=None, recursive=False):
    pathnames = []
    if recursive:
        for directory, dirnames, filenames in walk(rootdir):
            for filename in filenames:
                pathname = os.path.join(directory, filename)
                if pattern:
                    if re.search(pattern, filename):
                        pathnames.append(pathname)
                    else:
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
                        pass
                else:
                    pathnames.append(pathname)
    return pathnames


def find_local_maxima(a, x_axis, center, tolerance=0):
    x = []
    y = []
    indices = []
    for i in range(len(x_axis)):
        x_value = x_axis[i]
        if center - tolerance <= x_value <= center + tolerance:
            indices.append(i)
            x.append(x_value)
            y.append(a[i])
    if not x:
        raise 'no points within %s of %s'
    best_x_index = indices[0]
    best_x_value = x_axis[0]
    best_y_value = y[0]
    for i in range(len(x)):
        if best_y_value < y[i]:
            best_x_index = indices[i]
            best_x_value = x_axis[best_x_index]
            best_y_value = y[i]
    return best_y_value, best_x_value, best_x_index


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
    return left_index, right_index


def area_under_peak(spectrum, x_axis, x_index, boxcar_half_width=0):
    left_index, right_index = find_peak_feet_indices(spectrum, x_axis,
        x_index, boxcar_half_width)
    slope = float(spectrum[right_index] - spectrum[left_index]) / (x_axis[
        right_index] - x_axis[left_index])
    subspectrum = []
    subaxis = []
    for i in range(left_index, right_index + 1):
        baseline = spectrum[left_index] + slope * (x_axis[i] - x_axis[
            left_index])
        subspectrum.append(spectrum[i] - baseline)
        subaxis.append(x_axis[i])
    area = numpy.trapz(subspectrum, subaxis)
    return area


def peak_height_above_background(spectrum, x_axis, x_index, boxcar_half_width=0
    ):
    left_index, right_index = find_peak_feet_indices(spectrum, x_axis,
        x_index, boxcar_half_width)
    width_wn = x_axis[right_index] - x_axis[left_index]
    width_px = right_index - left_index + 1
    slope = float(spectrum[right_index] - spectrum[left_index]) / width_wn
    baseline = spectrum[left_index] + slope * (x_axis[x_index] - x_axis[
        left_index])
    height = spectrum[x_index] - baseline
    log.debug('peak_height_above_background: peak at x_index %d (boxcar %d)',
        x_index, boxcar_half_width)
    log.debug('peak_height_above_background:   abs height: %.2f', spectrum[
        x_index])
    log.debug('peak_height_above_background:   peak width: (%d px, %.2f cm-1)',
        width_px, width_wn)
    log.debug('peak_height_above_background:   feet: (%d, %d)', left_index,
        right_index)
    log.debug('peak_height_above_background:   feet height: (%.2f, %.2f)',
        spectrum[left_index], spectrum[right_index])
    log.debug('peak_height_above_background:   slope: %.2f', slope)
    log.debug('peak_height_above_background:   peak baseline: %.2f', baseline)
    log.debug('peak_height_above_background:   relative height: %.2f', height)
    return height, width_wn, width_px


def find_nearest_index(L, value):
    a = numpy.asarray(L)
    return numpy.abs(a - value).argmin()


def find_nearest_value(L, value):
    i = find_nearest_index(L, value)
    return L[i]


def interpolate_array(spectrum, old_axis, new_axis):
    if not spectrum or not old_axis or not new_axis or len(spectrum) != len(
        old_axis) or len(new_axis) < 1:
        return
    return numpy.interp(new_axis, old_axis, spectrum)


def interpolate_value(spectrum, old_axis, x):
    if not spectrum or not old_axis or not new_axis or len(spectrum) != len(
        old_axis) or len(new_axis) < 1:
        return
    new_axis = [x - 1, x, x + 1]
    new_y = numpy.interp(new_axis, old_axis, spectrum)
    if new_y is not None and len(new_y) == len(new_axis):
        return new_y[1]


def ascii_spectrum(spectrum, rows, cols, x_axis, x_unit):
    spectral_min = min(spectrum)
    spectral_max = max(spectrum)
    spectral_avg = 1.0 * sum(spectrum) / len(spectrum)
    bins = [0] * cols
    for i in range(len(spectrum)):
        col = int(1.0 * cols * i / len(spectrum))
        bins[col] += spectrum[i] - spectral_min
    lines = []
    bin_hi = max(bins)
    for row in range(rows - 1, -1, -1):
        s = '| '
        for col in range(cols):
            s += '*' if bins[col] >= 1.0 * row / rows * bin_hi else ' '
        lines.append(s)
    lines.append('+-' + '-' * cols)
    lines.append(
        '  Min: %8.2f  Max: %8.2f  Mean: %8.2f  (range %.2f, %.2f%s)' % (
        spectral_min, spectral_max, spectral_avg, x_axis[0], x_axis[-1],
        x_unit))
    return lines


def timestamp():
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S')


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


def coeffs_look_valid(coeffs, count=None):
    if coeffs is None:
        log.debug('no coeffs, returning False')
        return False
    if count is not None and len(coeffs) != count:
        log.debug('coeff count is wrong, returning False')
        return False
    for i in range(len(coeffs)):
        if math.isnan(coeffs[i]):
            log.debug('found NaN in coeff, returning False')
            return False
    all_default = True
    for i in range(len(coeffs)):
        if i == 1:
            if coeffs[i] != 1.0:
                all_default = False
        elif coeffs[i] != 0.0:
            all_default = False
    if all_default:
        log.debug('coeffs all default, returning False')
        return False
    all_const = True
    log.info(coeffs)
    for i in range(1, len(coeffs)):
        if coeffs[0] != coeffs[i]:
            all_const = False
    if all_const:
        log.debug('coeffs all const, returning False')
        return False
    return True


def stomp_first(a, count):
    for i in range(count):
        a[i] = a[count]


def stomp_last(a, count):
    for i in range(count):
        a[-(i + 1)] = a[-(count + 1)]


def clamp_to_int16(n):
    return max(-32768, min(32767, int(n)))


def parabolic_approximation(pixel, x, y):
    if len(x) != len(y):
        log.error('parabolic approximation array lengths differ')
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
        log.debug('parabolic approximation: peak misformed or saturated')
    denom = (x1 - x2) * (x1 - x3) * (x2 - x3)
    A = (x3 * (y2 - y1) + x2 * (y1 - y3) + x1 * (y3 - y2)) / denom
    B = (x3 * x3 * (y1 - y2) + x2 * x2 * (y3 - y1) + x1 * x1 * (y2 - y3)
        ) / denom
    C = (x2 * x3 * (x2 - x3) * y1 + x3 * x1 * (x3 - x1) * y2 + x1 * x2 * (
        x1 - x2) * y3) / denom
    vertex_x = -B / (2 * A)
    vertex_y = C - B * B / (4 * A)
    if vertex_x < x1 or vertex_x > x3:
        log.error('parabolic approximation failed (x exceeded limits)')
        return 0, 0
    if vertex_x == x2:
        return x[x2], vertex_y
    elif vertex_x < x2:
        left = x1
        right = x2
    else:
        left = x2
        right = x3
    x_coord = x[left] + (x[right] - x[left]) * (vertex_x - left)
    log.debug('parabolic approximation: x1 %d, x2 %d, x3 %d', x1, x2, x3)
    log.debug('parabolic approximation: x.x1 %.2f, x.x2 %.2f, x.x3 %.2f', x
        [x1], x[x2], x[x3])
    log.debug('parabolic approximation: y.x1 %.2f, y.x2 %.2f, y.x3 %.2f', y
        [x1], y[x2], y[x3])
    log.debug('parabolic approximation: vertex_x %.2f, vertex_y %.2f',
        vertex_x, vertex_y)
    log.debug('parabolic approximation: left %d, right %d', left, right)
    log.debug('parabolic approximation: x.left %.2f, x.right %.2f', x[left],
        x[right])
    log.debug('parabolic approximation: x.coord %.2f', x_coord)
    return x_coord, vertex_y


def twos_complement(val, bits):
    if val & 1 << bits - 1 != 0:
        val = val - (1 << bits)
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
        a.append(n & 255)
        a.append(n >> 8 & 255)
    return a
