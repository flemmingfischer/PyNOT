"""
Automatically Classify and Reduce a given Data Set
"""

from astropy.io import fits
from argparse import ArgumentParser
from collections import defaultdict
import os
import sys
import datetime
import yaml

import alfosc
import data_organizer as do
from calibs import combine_bias_frames, combine_flat_frames, normalize_spectral_flat
from extraction import auto_extract
import extract_gui
from wavecal import rectify
from identify_gui import create_pixtable
from scired import raw_correction, auto_fit_background, correct_cosmics
from response import calculate_response, flux_calibrate

from PyQt5.QtWidgets import QApplication

code_dir = os.path.dirname(os.path.abspath(__file__))
calib_dir = os.path.join(code_dir, 'calib/')
defaults_fname = os.path.join(calib_dir, 'default_options.yml')
v_file = os.path.join(code_dir, 'VERSION')
with open(v_file) as version_file:
    __version__ = version_file.read().strip()


class Report(object):
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.time = datetime.datetime.now()
        self.fname = 'pynot_%s.log' % self.time.strftime('%d%b%Y-%Hh%Mm%S')
        self.remarks = list()
        self.lines = list()
        self.header = """
        #  PyNOT Data Processing Pipeline
        # ================================
        # version %s
        %s

        """ % (__version__, self.time.strftime("%b %d, %Y  %H:%M:%S"))
        self.report = ""

        if self.verbose:
            print(self.header)

    def clear(self):
        self.lines = list()
        self.remarks = list()

    def set_filename(self, fname):
        self.fname = fname

    def commit(self, text):
        if self.verbose:
            print(text)
        self.lines.append(text)

    def error(self, text):
        text = ' [ERROR]  - ' + text
        if self.verbose:
            print(text)
        if text[-1] != '\n':
            text += '\n'
        self.lines.append(text)

    def warn(self, text):
        text = '[WARNING] - ' + text
        if self.verbose:
            print(text)
        if text[-1] != '\n':
            text += '\n'
        self.lines.append(text)

    def write(self, text, prefix='          - '):
        text = prefix + text
        if self.verbose:
            print(text)
        if text[-1] != '\n':
            text += '\n'
        self.lines.append(text)

    def add_linebreak(self):
        if self.verbose:
            print("")
        self.lines.append("\n")

    def add_remark(self, text):
        self.remarks.append(text)

    def _make_report(self):
        remark_str = ''.join(self.remarks)
        lines_str = ''.join(self.lines)
        self.report = '\n'.join([self.header, remark_str, lines_str])

    def print_report(self):
        self._make_report()
        print(self.report)

    def save(self):
        self._make_report()
        with open(self.fname, 'w') as output:
            output.write(self.report)

    def exit(self):
        print(" - Pipeline terminated.")
        print(" Consult the log: %s\n" % self.fname)
        self.save()

    def fatal_error(self):
        print(" !! FATAL ERROR !!")
        print(" Consult the log: %s\n" % self.fname)
        self.save()


class State(dict):
    """A collection of variables for the pipeline, such as arc line ID tables etc."""
    def __init__(self):
        dict.__init__(self, {})
        self.current = ''

    def print_current_state(self):
        print(self.current)

    def set_current_state(self, state):
        self.current = state


def get_options(option_fname):
    with open(option_fname) as opt_file:
        options = yaml.full_load(opt_file)
    return options


def main(raw_path=None, options_fname=None, verbose=False, interactive=False):
    log = Report(verbose)
    status = State()

    global app
    app = QApplication(sys.argv)

    # -- Parse Options from YAML
    options = get_options(defaults_fname)

    if options_fname:
        user_options = get_options(options_fname)
        for section_name, section in user_options.items():
            if isinstance(section, dict):
                options[section_name].update(section)
            else:
                options[section_name] = section
        if not raw_path:
            raw_path = user_options['path']

    if interactive:
        # Set all interactive steps to True
        options['identify']['interactive'] = True
        options['identify']['all'] = True
        options['extract']['interactive'] = True
        options['response']['interactive'] = True

    if not os.path.exists(raw_path):
        log.error("Data path does not exist : %s" % raw_path)
        log.fatal_error()
        return

    dataset_fname = options['dataset']
    if os.path.exists(dataset_fname):
        # -- load collection
        database = do.io.load_database(dataset_fname)
        log.write("Loaded file classification database: %s" % dataset_fname)
        # -- reclassify (takes already identified files into account)

    else:
        # Classify files:
        log.write("Classyfying files in folder: %s" % raw_path)
        try:
            database, message = do.classify(raw_path, progress=verbose)
            do.io.save_database(database, dataset_fname)
            log.commit(message)
            log.write("Saved file classification database: %s" % dataset_fname)
        except ValueError as err:
            log.error(str(err))
            print(err)
            log.fatal_error()
            return
        except FileNotFoundError as err:
            log.error(str(err))
            log.fatal_error()
            return

    # -- Organize object files in dataset:
    object_filelist = database['SPEC_OBJECT']
    object_images = list(map(do.RawImage, object_filelist))

    log.add_linebreak()
    log.write(" - The following objects were found in the dataset:", prefix='')
    log.write("      OBJECT           GRISM        SLIT      EXPTIME       FILENAME", prefix='')
    for sci_img in object_images:
        output_variables = (sci_img.object, sci_img.grism, sci_img.slit, sci_img.exptime, os.path.basename(sci_img.filename))
        log.write("%20s  %9s  %11s   %5.0f  %s" % output_variables, prefix='')
    log.add_linebreak()

    # get list of unique grisms in dataset:
    grism_list = list()
    for sci_img in object_images:
        grism_name = alfosc.grism_translate[sci_img.grism]
        if grism_name not in grism_list:
            grism_list.append(grism_name)

    # -- Check arc line files:
    arc_images = list()
    # for arc_type in ['ARC_He', 'ARC_HeNe', 'ARC_Ne', 'ARC_ThAr']:
    for arc_type in ['ARC_HeNe', 'ARC_ThAr']:
        # For now only HeNe arc lines are accepted!
        # Implement ThAr and automatic combination of He + Ne
        if arc_type in database.keys():
            arc_images += database[arc_type]

    arc_images_for_grism = defaultdict(list)
    for arc_img in arc_images:
        raw_grism = fits.getheader(arc_img)['ALGRNM']
        this_grism = alfosc.grism_translate[raw_grism]
        arc_images_for_grism[this_grism].append(arc_img)

    for grism_name in grism_list:
        if len(arc_images_for_grism[grism_name]) == 0:
            log.error("No arc frames defined for grism: %s" % grism_name)
            log.fatal_error()
            return
        else:
            log.write("%s has necessary arc files." % grism_name)

    log.add_linebreak()
    identify_all = options['identify']['all']
    identify_interactive = options['identify']['interactive']
    if identify_interactive and identify_all:
        grisms_to_identify = []
        log.write("Identify: interactively reidentify arc lines for all objects")
        log.add_linebreak()

    elif identify_interactive and not identify_all:
        # Make pixeltable for all grisms:
        grisms_to_identify = grism_list
        log.write("Identify: interactively identify all grisms in dataset:")
        log.write(", ".join(grisms_to_identify))
        log.add_linebreak()

    else:
        # Check if pixeltables exist:
        grisms_to_identify = []
        for grism_name in grism_list:
            pixtab_fname = os.path.join(calib_dir, '%s_pixeltable.dat' % grism_name)
            if not os.path.exists(pixtab_fname):
                log.write("%s : pixel table does not exist. Will identify lines..." % grism_name)
                grisms_to_identify.append(grism_name)
            else:
                log.write("%s : pixel table already exists" % grism_name)
                status[grism_name+'_pixtab'] = pixtab_fname
                status[pixtab_fname] = options['identify']['order_wl']
        log.add_linebreak()


    # Identify interactively for grisms that are not defined
    # add the new pixel tables to the calib cache for future use
    for grism_name in grisms_to_identify:
        log.write("Starting interactive definition of pixel table for %s" % grism_name)
        try:
            arc_fname = arc_images_for_grism[grism_name][0]
            if grism_name+'_pixtab' in options:
                pixtab_fname = options[grism_name+'_pixtab']
            else:
                pixtab_fname = os.path.join(calib_dir, '%s_pixeltable.dat' % grism_name)
            # Check arc line type:
            file_type = database.file_database[arc_fname]
            if 'HeNe' in file_type:
                linelist_fname = os.path.join(calib_dir, 'HeNe_linelist.dat')
            elif 'ThAr' in file_type:
                linelist_fname = os.path.join(calib_dir, 'ThAr_linelist.dat')
            else:
                log.error("No reference linelist found! Something went wrong!")
                linelist_fname = ''
            poly_order, saved_pixtab_fname, msg = create_pixtable(arc_fname, grism_name,
                                                                  pixtab_fname, linelist_fname,
                                                                  order_wl=options['identify']['order_wl'],
                                                                  app=app)
            status[saved_pixtab_fname] = poly_order
            status[grism_name+'_pixtab'] = saved_pixtab_fname
            log.commit(msg)
        except:
            log.error("Identification of arc lines failed!")
            log.fatal_error()
            log.save()
            print("Unexpected error:", sys.exc_info()[0])
            raise


    # Save overview log:
    print("")
    print(" - Pipeline setup ended successfully.")
    print("   Consult the overview log: %s\n\n" % log.fname)
    log.save()


    # ------------------------------------------------------------------
    # -- Start Main Reduction:
    for sci_img in object_images[:1]:                                       # <-- FULL LOOP BEFORE RELEASE
        # Create working directory:
        raw_base = os.path.basename(sci_img.filename).split('.')[0][2:]
        output_dir = sci_img.target_name + '_' + raw_base
        if not os.path.exists(output_dir):
            os.mkdir(output_dir)

        # Start new log in working directory:
        log_fname = os.path.join(output_dir, 'pynot.log')
        log.clear()
        log.set_filename(log_fname)
        log.write("Starting PyNOT Longslit Spectroscopic Reduction")
        log.add_linebreak()
        log.write("Target Name: %s" % sci_img.target_name)
        log.write("Input Filename: %s" % sci_img.filename)
        log.write("Saving output to directory: %s" % output_dir)

        # Prepare output filenames:
        grism = alfosc.grism_translate[sci_img.grism]
        master_bias_fname = os.path.join(output_dir, 'MASTER_BIAS.fits')
        comb_flat_fname = os.path.join(output_dir, 'FLAT_COMBINED_%s_%s.fits' % (grism, sci_img.slit))
        norm_flat_fname = os.path.join(output_dir, 'NORM_FLAT_%s_%s.fits' % (grism, sci_img.slit))
        rect2d_fname = os.path.join(output_dir, 'RECT2D_%s.fits' % (sci_img.target_name))
        bgsub2d_fname = os.path.join(output_dir, 'BGSUB2D_%s.fits' % (sci_img.target_name))
        response_pdf = os.path.join(output_dir, 'RESPONSE_%s.pdf' % (grism))
        corrected_2d_fname = os.path.join(output_dir, 'CORRECTED2D_%s.fits' % (sci_img.target_name))
        flux2d_fname = os.path.join(output_dir, 'FLUX2D_%s.fits' % (sci_img.target_name))
        flux1d_fname = os.path.join(output_dir, 'FLUX1D_%s.fits' % (sci_img.target_name))
        extract_pdf_fname = os.path.join(output_dir, 'extract1D_details.pdf')

        # Combine Bias Frames matched for CCD setup:
        bias_frames = sci_img.match_files(database['BIAS'])
        if len(bias_frames) < 3:
            log.warn("Must have at least 3 bias frames to combine, not %i" % len(bias_frames))
            if 'master_bias' in options['bias']:
                log.warn("Using master bias frame: %s" % options['bias']['master_bias'])
                master_bias_fname = options['bias']['master_bias']
                status['master_bias'] = master_bias_fname
            else:
                log.error("No backup bias frame! Either provide more than 3 bias frames")
                log.error("or provide one using the option: bias.master_bias")
                log.fatal_error()
                return
        else:
            log.write("Running task: Bias Combination")
            try:
                _, bias_msg = combine_bias_frames(bias_frames, output=master_bias_fname,
                                                  kappa=options['bias']['kappa'], overwrite=True)
                log.commit(bias_msg)
                log.add_linebreak()
                status['master_bias'] = master_bias_fname
            except:
                log.error("Median combination of bias frames failed!")
                log.fatal_error()
                print("Unexpected error:", sys.exc_info()[0])
                raise


        # Combine Flat Frames matched for CCD setup, grism, slit and filter:
        flat_frames = sci_img.match_files(database['SPEC_FLAT'], grism=True, slit=True, filter=True)
        if len(flat_frames) == 0:
            log.error("No flat frames provided!")
            log.fatal_error()
            return

        try:
            log.write("Running task: Spectral Flat Combination")
            _, flat_msg = combine_flat_frames(flat_frames, comb_flat_fname, mbias=master_bias_fname,
                                              kappa=options['flat']['kappa'], overwrite=True,
                                              mode='spec', dispaxis=sci_img.dispaxis)
            log.commit(flat_msg)
            log.add_linebreak()
            status['flat_combined'] = comb_flat_fname
        except ValueError as err:
            log.commit(str(err)+'\n')
            log.fatal_error()
            return
        except:
            log.error("Combination of flat frames failed!")
            log.fatal_error()
            print("Unexpected error:", sys.exc_info()[0])
            raise


        # Normalize the spectral flat field:
        try:
            log.write("Running task: Spectral Flat Normalization")
            _, norm_msg = normalize_spectral_flat(comb_flat_fname, output=norm_flat_fname,
                                                  fig_dir=output_dir, axis=sci_img.dispaxis,
                                                  lower=options['flat']['lower'], upper=options['flat']['upper'],
                                                  order=options['flat']['order'], sigma=options['flat']['sigma'],
                                                  plot=options['flat']['plot'], show=options['flat']['show'],
                                                  overwrite=True)
            log.commit(norm_msg)
            log.add_linebreak()
            status['flat_normalized'] = norm_flat_fname
        except:
            log.error("Normalization of flat frames failed!")
            log.fatal_error()
            print("Unexpected error:", sys.exc_info()[0])
            raise


        # Identify lines in arc frame:
        arc_fname, = sci_img.match_files(arc_images, grism=True, slit=True, filter=True, get_closest_time=True)
        if identify_interactive and identify_all:
            log.write("Running task: Arc Line Identification")
            try:
                if grism+'_pixtab' in options:
                    pixtab_fname = options[grism_name+'_pixtab']
                else:
                    pixtab_fname = os.path.join(calib_dir, '%s_pixeltable.dat' % grism)

                file_type = database.file_database[arc_fname]
                if 'HeNe' in file_type:
                    linelist_fname = os.path.join(calib_dir, 'HeNe_linelist.dat')
                elif 'ThAr' in file_type:
                    linelist_fname = os.path.join(calib_dir, 'ThAr_linelist.dat')
                else:
                    log.error("No reference linelist found! Something went wrong!")
                    linelist_fname = ''
                order_wl, pixtable, msg = create_pixtable(arc_fname, grism,
                                                          pixtab_fname, linelist_fname,
                                                          order_wl=options['identify']['order_wl'],
                                                          app=app)
                status[pixtable] = order_wl
                status[grism+'_pixtab'] = pixtable
                log.commit(msg)
            except:
                log.error("Identification of arc lines failed!")
                log.fatal_error()
                print("Unexpected error:", sys.exc_info()[0])
                raise
        else:
            # -- or use previous line identifications
            pixtable = status[grism+'_pixtab']
            order_wl = status[pixtable]


        # Response Function:
        flux_std_files = sci_img.match_files(database['SPEC_FLUX-STD'], grism=True, slit=True, filter=True, get_closest_time=True)
        if len(flux_std_files) == 0:
            log.warn("No spectroscopic standard star was found in the dataset!")
            log.warn("The reduced spectra will not be flux calibrated")
            status['RESPONSE'] = None

        else:
            std_fname = flux_std_files[0]
            response_fname = os.path.join(output_dir, 'response_%s.fits' % (grism))
            if os.path.exists(response_fname) and not options['response']['force']:
                log.write("Response function already exists: %s" % response_fname)
                log.add_linebreak()
                status['RESPONSE'] = response_fname
            else:
                std_fname = flux_std_files[0]
                log.write("Running task: Calculation of Response Function")
                log.write("Spectroscopic Flux Standard: %s" % std_fname)
                try:
                    response_fname, response_msg = calculate_response(std_fname, arc_fname=arc_fname,
                                                                      pixtable_fname=pixtable,
                                                                      bias_fname=master_bias_fname,
                                                                      flat_fname=norm_flat_fname,
                                                                      output=response_fname,
                                                                      output_dir=output_dir, pdf_fname=response_pdf,
                                                                      order=options['response']['order'],
                                                                      interactive=options['response']['interactive'],
                                                                      dispaxis=sci_img.dispaxis, order_wl=order_wl,
                                                                      order_bg=options['scired']['order_bg'],
                                                                      rectify_options=options['rectify'],
                                                                      app=app)
                    status['RESPONSE'] = response_fname
                    log.commit(response_msg)
                except:
                    log.error("Calculation of response function failed!")
                    log.fatal_error()
                    print("Unexpected error:", sys.exc_info()[0])
                    raise


        # Bias correction, Flat correction
        log.write("Running task: Bias and Flat Field Correction")
        try:
            output_msg = raw_correction(sci_img.data, sci_img.header, master_bias_fname, norm_flat_fname,
                                        output=corrected_2d_fname, overwrite=True, overscan=50)
            log.commit(output_msg)
        except:
            log.error("Bias and flat field correction failed!")
            log.fatal_error()
            print("Unexpected error:", sys.exc_info()[0])
            raise


        # Call rectify
        log.write("Running task: 2D Rectification and Wavelength Calibration")
        try:
            rect_msg = rectify(corrected_2d_fname, arc_fname, pixtable, output=rect2d_fname, fig_dir=output_dir,
                               dispaxis=sci_img.dispaxis, order_wl=order_wl, **options['rectify'])
            log.commit(rect_msg)
        except:
            log.error("2D rectification failed!")
            log.fatal_error()
            print("Unexpected error:", sys.exc_info()[0])
            raise


        # Automatic Background Subtraction:
        bgsub_pdf_name = os.path.join(output_dir, 'bgsub2D.pdf')
        log.write("Running task: Background Subtraction")
        try:
            bg_msg = auto_fit_background(rect2d_fname, bgsub2d_fname, dispaxis=1,
                                         kappa=10, fwhm_scale=4, xmin=20,
                                         order_bg=options['scired']['order_bg'], plot_fname=bgsub_pdf_name)
            log.commit(bg_msg)
        except:
            log.error("Automatic background subtraction failed!")
            log.fatal_error()
            print("Unexpected error:", sys.exc_info()[0])
            raise


        # Correct Cosmic Rays Hits:
        if options['scired']['crr']:
            log.write("Running task: Cosmic Ray Rejection")
            crr_fname = os.path.join(output_dir, 'CRR_BGSUB2D_%s.fits' % (sci_img.target_name))
            try:
                crr_msg = correct_cosmics(bgsub2d_fname, crr_fname, niter=options['scired']['niter'],
                                          gain=options['scired']['gain'], readnoise=options['scired']['readnoise'])
                log.commit(crr_msg)
            except:
                log.error("Cosmic ray correction failed!")
                log.fatal_error()
                print("Unexpected error:", sys.exc_info()[0])
                raise
        else:
            crr_fname = bgsub2d_fname


        # Flux Calibration:
        if status['RESPONSE']:
            log.write("Running task: Flux Calibration")
            response_fname = status['RESPONSE']
            try:
                flux_msg = flux_calibrate(crr_fname, output=flux2d_fname, response=response_fname)
                log.commit(flux_msg)
                status['FLUX2D'] = flux2d_fname
            except:
                log.error("Flux calibration failed!")
                log.fatal_error()
                print("Unexpected error:", sys.exc_info()[0])
                raise
        else:
            status['FLUX2D'] = crr_fname


        # Extract 1D spectrum:
        log.write("Running task: 1D Extraction")
        extract_fname = status['FLUX2D']
        if options['extract']['interactive']:
            try:
                log.write("Starting Graphical User Interface")
                extract_gui.run_gui(extract_fname, output_fname=flux1d_fname,
                                    app=app, **options['extract'])
                log.write("Writing fits table: %s" % flux1d_fname, prefix=" [OUTPUT] - ")
            except:
                log.error("Interactive 1D extraction failed!")
                log.fatal_error()
                print("Unexpected error:", sys.exc_info()[0])
                raise
        else:
            try:
                ext_msg = auto_extract(extract_fname, flux1d_fname, dispaxis=1, pdf_fname=extract_pdf_fname,
                                       **options['extract'])
                log.commit(ext_msg)
            except:
                log.error("Spectral 1D extraction failed!")
                log.fatal_error()
                print("Unexpected error:", sys.exc_info()[0])
                raise

        log.exit()
        break


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument("path", type=str, nargs='?', default='',
                        help="Path to directory containing the raw data")
    parser.add_argument("--options", type=str, default='',
                        help="Filename of options in YAML format")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print log to terminal")
    parser.add_argument("-I", "--interactive", action="store_true",
                        help="Use interactive interface throughout")
    args = parser.parse_args()

    if args.path:
        main(args.path, options_fname=args.options, verbose=args.verbose, interactive=args.interactive)

    elif args.options:
        main(options_fname=args.options, verbose=args.verbose, interactive=args.interactive)

    else:
        print("\n  Running PyNOT Data Processing Pipeline\n")
        if not os.path.exists('options.yml'):
            copy_cmd = "cp %s options.yml" % defaults_fname
            os.system(copy_cmd)
            print(" - Created the default option file: options.yml")
        message = """
         - Update the file and run PyNOT as:
            %] pynot --options options.yml

        Otherwise provide a path to the raw data:
            %] pynot path/to/data

        """
        print(message)
