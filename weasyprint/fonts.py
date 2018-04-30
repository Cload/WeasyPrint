"""
    weasyprint.fonts
    ----------------

    Interface with external libraries managing fonts installed on the system.

    :copyright: Copyright 2011-2016 Simon Sapin and contributors, see AUTHORS.
    :license: BSD, see LICENSE for details.

"""

import os
import sys
import tempfile
import warnings

from .logger import LOGGER
from .text import (
    cairo, dlopen, ffi, get_font_features, gobject, pango, pangocairo)
from .urls import fetch

# Cairo crashes with font-size: 0 when using Win32 API
# See https://github.com/Kozea/WeasyPrint/pull/599
# Set to True on startup when fontconfig is inoperable.
# Used by text/Layout() to mask font-size: 0 with a font_size of 1.
ZERO_FONTSIZE_CRASHES_CAIRO = False


class FontConfiguration:
    """Font configuration"""

    def __init__(self):
        """Create a font configuration before rendering a document."""
        self.font_map = None

    def add_font_face(self, rule_descriptors, url_fetcher):
        """Add a font into the application."""


fontconfig = None
pangoft2 = None

if pango.pango_version() < 13800:
    warnings.warn('@font-face support needs Pango >= 1.38')
else:
    try:
        fontconfig = dlopen(ffi, 'fontconfig', 'libfontconfig',
                            'libfontconfig-1.dll',
                            'libfontconfig.so.1', 'libfontconfig-1.dylib')
        pangoft2 = dlopen(ffi, 'pangoft2-1.0', 'libpangoft2-1.0-0',
                          'libpangoft2-1.0.so', 'libpangoft2-1.0.dylib')
    except Exception as exception:
        warnings.warn('@font-face not supported: {0}'.format(exception))
        fontconfig = None
        pangoft2 = None


if fontconfig and pangoft2:
    ffi.cdef('''
        // FontConfig

        typedef int FcBool;
        typedef struct _FcConfig FcConfig;
        typedef struct _FcPattern FcPattern;
        typedef struct _FcStrList FcStrList;
        typedef unsigned char FcChar8;

        typedef enum {
            FcResultMatch, FcResultNoMatch, FcResultTypeMismatch, FcResultNoId,
            FcResultOutOfMemory
        } FcResult;

        typedef enum {
            FcMatchPattern, FcMatchFont, FcMatchScan
        } FcMatchKind;


        typedef struct _FcFontSet {
            int nfont;
            int sfont;
            FcPattern **fonts;
        } FcFontSet;

        typedef enum _FcSetName {
            FcSetSystem = 0,
            FcSetApplication = 1
        } FcSetName;

        FcConfig * FcInitLoadConfigAndFonts (void);
        void FcConfigDestroy (FcConfig *config);
        FcBool FcConfigAppFontAddFile (
            FcConfig *config, const FcChar8 *file);
        FcConfig * FcConfigGetCurrent (void);
        FcBool FcConfigSetCurrent (FcConfig *config);
        FcBool FcConfigParseAndLoad (
            FcConfig *config, const FcChar8 *file, FcBool complain);

        FcFontSet * FcConfigGetFonts(FcConfig *config, FcSetName set);
        FcStrList * FcConfigGetConfigFiles(FcConfig *config);
        FcChar8 * FcStrListNext(FcStrList *list);

        void FcDefaultSubstitute (FcPattern *pattern);
        FcBool FcConfigSubstitute (
            FcConfig *config, FcPattern *p, FcMatchKind kind);

        FcPattern * FcPatternCreate (void);
        FcPattern * FcPatternDestroy (FcPattern *p);
        FcBool FcPatternAddString (
            FcPattern *p, const char *object, const FcChar8 *s);
        FcResult FcPatternGetString (
            FcPattern *p, const char *object, int n, FcChar8 **s);
        FcPattern * FcFontMatch (
            FcConfig *config, FcPattern *p, FcResult *result);


        // PangoFT2

        typedef ... PangoFcFontMap;

        void pango_fc_font_map_set_config (
            PangoFcFontMap *fcfontmap, FcConfig *fcconfig);
        void pango_fc_font_map_shutdown (PangoFcFontMap *fcfontmap);


        // PangoCairo

        typedef ... PangoCairoFontMap;

        void pango_cairo_font_map_set_default (PangoCairoFontMap *fontmap);
        PangoFontMap * pango_cairo_font_map_new_for_font_type (
            cairo_font_type_t fonttype);
    ''')

    FONTCONFIG_WEIGHT_CONSTANTS = {
        'normal': 'normal',
        'bold': 'bold',
        100: 'thin',
        200: 'extralight',
        300: 'light',
        400: 'normal',
        500: 'medium',
        600: 'demibold',
        700: 'bold',
        800: 'extrabold',
        900: 'black',
    }

    FONTCONFIG_STYLE_CONSTANTS = {
        'normal': 'roman',
        'italic': 'italic',
        'oblique': 'oblique',
    }

    FONTCONFIG_STRETCH_CONSTANTS = {
        'normal': 'normal',
        'ultra-condensed': 'ultracondensed',
        'extra-condensed': 'extracondensed',
        'condensed': 'condensed',
        'semi-condensed': 'semicondensed',
        'semi-expanded': 'semiexpanded',
        'expanded': 'expanded',
        'extra-expanded': 'extraexpanded',
        'ultra-expanded': 'ultraexpanded',
    }

    def _check_font_configuration(font_config, warn=False):
        """Check whether the given font_config has fonts.

        The default fontconfig configuration file may be missing, particularly
        on Windows, giving "Fontconfig error: Cannot load default config file".

        No default config == No fonts.
        No fonts == expect ugly output.
        If you happen to have an html without a valid @font-face all
        letters turn into rectangles.
        If you happen to have an html with at least one valid @font-face
        all text is styled with that font.

        """
        # Nobody ever complained about such a situation on Unix-like OSes.
        if not sys.platform.startswith('win'):
            return True

        fonts = fontconfig.FcConfigGetFonts(
            font_config, fontconfig.FcSetSystem)
        if fonts.nfont > 0:
            return True

        if warn:
            config_files = fontconfig.FcConfigGetConfigFiles(font_config)
            config_file = fontconfig.FcStrListNext(config_files)
            if config_file == ffi.NULL:
                warnings.warn(
                    '@font-face not supported: '
                    'Cannot load default config file')
            else:
                warnings.warn('@font-face not supported: no fonts configured')

        # TODO: on Windows we could try to add the system fonts like that:
        # fontdir = os.path.join(os.environ['WINDIR'], 'Fonts')
        # fontconfig.FcConfigAppFontAddDir(
        #     font_config,
        #     # not shure which encoding fontconfig expects
        #     fontdir.encode('mbcs'))

        # Fall back to default @font-face-less behaviour.
        return False

    class FontConfiguration(FontConfiguration):
        def __init__(self):
            """Create a FT2 font configuration.

            See Behdad's blog:
            https://mces.blogspot.fr/2015/05/
                    how-to-use-custom-application-fonts.html

            """
            # Load the master config file and the fonts.
            self._fontconfig_config = ffi.gc(
                fontconfig.FcInitLoadConfigAndFonts(),
                fontconfig.FcConfigDestroy)
            if _check_font_configuration(self._fontconfig_config):
                self.font_map = ffi.gc(
                    pangocairo.pango_cairo_font_map_new_for_font_type(
                        cairo.FONT_TYPE_FT),
                    gobject.g_object_unref)
                pangoft2.pango_fc_font_map_set_config(
                    ffi.cast('PangoFcFontMap *', self.font_map),
                    self._fontconfig_config)
                # pango_fc_font_map_set_config keeps a reference to config
                fontconfig.FcConfigDestroy(self._fontconfig_config)
            else:
                self.font_map = None
            # On Windows the font tempfiles cannot be deleted,
            # putting them in a subfolder made my life easier.
            self._tempdir = None
            if sys.platform.startswith('win'):
                self._tempdir = os.path.join(
                    tempfile.gettempdir(), 'weasyprint')
                try:
                    os.mkdir(self._tempdir)
                except FileExistsError:
                    pass
                except Exception:
                    # Back to default.
                    self._tempdir = None
            self._filenames = []

        def add_font_face(self, rule_descriptors, url_fetcher):
            if self.font_map is None:
                return
            for font_type, url in rule_descriptors['src']:
                if url is None:
                    continue
                if font_type in ('external', 'local'):
                    config = self._fontconfig_config
                    fetch_as_url = True
                    if font_type == 'local':
                        font_name = url.encode('utf-8')
                        pattern = ffi.gc(
                            fontconfig.FcPatternCreate(),
                            fontconfig.FcPatternDestroy)
                        fontconfig.FcConfigSubstitute(
                            config, pattern, fontconfig.FcMatchFont)
                        fontconfig.FcDefaultSubstitute(pattern)
                        fontconfig.FcPatternAddString(
                            pattern, b'fullname', font_name)
                        fontconfig.FcPatternAddString(
                            pattern, b'postscriptname', font_name)
                        family = ffi.new('FcChar8 **')
                        postscript = ffi.new('FcChar8 **')
                        result = ffi.new('FcResult *')
                        matching_pattern = fontconfig.FcFontMatch(
                            config, pattern, result)
                        # TODO: do many fonts have multiple family values?
                        fontconfig.FcPatternGetString(
                            matching_pattern, b'fullname', 0, family)
                        fontconfig.FcPatternGetString(
                            matching_pattern, b'postscriptname', 0, postscript)
                        family = ffi.string(family[0])
                        postscript = ffi.string(postscript[0])
                        if font_name.lower() in (
                                family.lower(), postscript.lower()):
                            filename = ffi.new('FcChar8 **')
                            matching_pattern = fontconfig.FcFontMatch(
                                config, pattern, result)
                            fontconfig.FcPatternGetString(
                                matching_pattern, b'file', 0, filename)
                            # Can't use urlopen('file://...') on Windows.
                            # Fails with
                            # URLError: <urlopen error file on local host>
                            if sys.platform.startswith('win'):
                                fetch_as_url = False
                                url = ffi.string(filename[0]).decode(
                                    sys.getfilesystemencoding())
                            else:
                                url = (
                                    'file://' +
                                    ffi.string(filename[0]).decode('utf-8'))
                        else:
                            LOGGER.warning(
                                'Failed to load local font "%s"',
                                font_name.decode('utf-8'))
                            continue
                    try:
                        if fetch_as_url:
                            with fetch(url_fetcher, url) as result:
                                if 'string' in result:
                                    font = result['string']
                                else:
                                    font = result['file_obj'].read()
                        else:
                            with open(url, 'rb') as fd:
                                font = fd.read()
                    except Exception as exc:
                        LOGGER.error(
                            'Failed to load font at "%s" (%s)', url, exc)
                        continue
                    font_features = {
                        rules[0][0].replace('-', '_'): rules[0][1] for rules in
                        rule_descriptors.get('font_variant', [])}
                    if 'font_feature_settings' in rule_descriptors:
                        font_features['font_feature_settings'] = (
                            rule_descriptors['font_feature_settings'])
                    features_string = ''
                    for key, value in get_font_features(
                            **font_features).items():
                        features_string += '<string>%s %s</string>' % (
                            key, value)
                    fd, filename = tempfile.mkstemp(dir=self._tempdir)
                    os.write(fd, font)
                    os.close(fd)
                    self._filenames.append(filename)
                    xml = '''<?xml version="1.0"?>
                    <!DOCTYPE fontconfig SYSTEM "fonts.dtd">
                    <fontconfig>
                      <match target="scan">
                        <test name="file" compare="eq">
                          <string>%s</string>
                        </test>
                        <edit name="family" mode="assign_replace">
                          <string>%s</string>
                        </edit>
                        <edit name="slant" mode="assign_replace">
                          <const>%s</const>
                        </edit>
                        <edit name="weight" mode="assign_replace">
                          <const>%s</const>
                        </edit>
                        <edit name="width" mode="assign_replace">
                          <const>%s</const>
                        </edit>
                      </match>
                      <match target="font">
                        <test name="file" compare="eq">
                          <string>%s</string>
                        </test>
                        <edit name="fontfeatures"
                              mode="assign_replace">%s</edit>
                      </match>
                    </fontconfig>''' % (
                        filename,
                        rule_descriptors['font_family'],
                        FONTCONFIG_STYLE_CONSTANTS[
                            rule_descriptors.get('font_style', 'normal')],
                        FONTCONFIG_WEIGHT_CONSTANTS[
                            rule_descriptors.get('font_weight', 'normal')],
                        FONTCONFIG_STRETCH_CONSTANTS[
                            rule_descriptors.get('font_stretch', 'normal')],
                        filename, features_string)
                    fd, conf_filename = tempfile.mkstemp(dir=self._tempdir)
                    # TODO: is this encoding OK?
                    os.write(fd, xml.encode('utf-8'))
                    os.close(fd)
                    self._filenames.append(conf_filename)
                    fontconfig.FcConfigParseAndLoad(
                        config, conf_filename.encode('ascii'), True)
                    font_added = fontconfig.FcConfigAppFontAddFile(
                        config, filename.encode('ascii'))
                    if font_added:
                        # TODO: We should mask local fonts with the same name
                        # too as explained in Behdad's blog entry.
                        # TODO: What about pango_fc_font_map_config_changed()
                        # as suggested in Behdad's blog entry?
                        # Though it seems to work without...
                        return filename
                    else:
                        LOGGER.error('Failed to load font at "%s"', url)
            LOGGER.warning(
                'Font-face "%s" cannot be loaded',
                rule_descriptors['font_family'])

        def __del__(self):
            """Clean a font configuration for a document."""
            # Can't cleanup the temporary font files on Windows, library has
            # still open file handles. On Unix `os.remove()` a file that is in
            # use works fine, on Windows a PermissionError is raised.
            # FcConfigAppFontClear and pango_fc_font_map_shutdown don't help.
            for filename in self._filenames:
                try:
                    os.remove(filename)
                except OSError:
                    continue

    _fontconfig_config = ffi.gc(
        fontconfig.FcInitLoadConfigAndFonts(),
        fontconfig.FcConfigDestroy)
    if not _check_font_configuration(_fontconfig_config, warn=True):
        warnings.warn('Expect ugly output with font-size: 0')
        ZERO_FONTSIZE_CRASHES_CAIRO = True
