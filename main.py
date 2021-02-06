"""
Automatically Classify and Reduce a given Data Set
"""

from argparse import ArgumentParser
from copy import copy
import os
import sys
import numpy as np

from functions import get_options, get_version_number

code_dir = os.path.dirname(os.path.abspath(__file__))
calib_dir = os.path.join(code_dir, 'calib/')
defaults_fname = os.path.join(calib_dir, 'default_options.yml')
parameters = get_options(defaults_fname)

__version__ = get_version_number()

# pynot  bias  files.list

def main():
    parser = ArgumentParser()
    recipes = parser.add_subparsers(dest='recipe')

    # -- BIAS :: Bias Combination
    parser_bias = recipes.add_parser('bias',
                                     help="Combine bias frames")
    parser_bias.add_argument("input", type=str,
                             help="Input file containing list of image filenames to combine")
    parser_bias.add_argument("-o", "output", type=str,
                             help="Output filename of combined bias frame")
    parser_bias.add_argument("--kappa", type=float, default=15,
                             help="Threshold for sigma clipping")

    # -- SFLAT :: Spectral Flat Combination
    parser_sflat = recipes.add_parser('sflat',
                                      help="Combine spectral flat frames")
    parser_sflat.add_argument("input", type=str,
                              help="Input file containing list of image filenames to combine")
    parser_sflat.add_argument("-o", "output", type=str,
                              help="Output filename of combined bias frame")
    # Define based on default options:
    for key, val in parameters['flat'].items():
        parser_sflat.add_argument("--%s" % key, type=type(val), default=val)

    # # -- IMFLAT :: Imaging Flat Combination
    # parser_imflat = recipes.add_parser('imflat',
    #                                    help="Combine imaging flat frames")

    # -- corr :: Raw Correction
    parser_corr = recipes.add_parser('corr',
                                     help="Apply bias subtraction, flat field correction and trimming")
    parser_corr.add_argument("input", type=str,
                             help="Input file containing list of image filenames to combine")
    parser_corr.add_argument("-o", "output", type=str,
                             help="Output filename")
    parser_corr.add_argument("--bias", type=str, required=True,
                             help="Filename of combined bias frame")
    parser_corr.add_argument("--flat", type=str, default='',
                             help="Filename of combined flat frame")


    # -- id :: Identify Arc Lines
    parser_id = recipes.add_parser('id',
                                   help="Interactive identification of arc lines")
    parser_id.add_argument("arc", type=str,
                           help="Input filename of arc line image")
    parser_id.add_argument("--lines", type=str, default='',
                           help="Linelist, automatically loaded if possible")
    parser_id.add_argument("--axis", type=int, default=2,
                           help="Dispersion axis: 1 horizontal, 2: vertical")


    # -- response :: Calculate Response Function
    parser_resp = recipes.add_parser('response',
                                     help="Interactive determination of instrument response function")
    parser_resp.add_argument("input", type=str,
                             help="Input filename of 1D spectrum of flux standard star")
    parser_resp.add_argument("-o", "--output", type=str, required=True,
                             help="Output filename of response function")


    # -- wave1d :: Wavelength Calibrate 1D Image
    parser_wave1 = recipes.add_parser('wave1d',
                                      help="Apply wavelength calibration to 1D spectra (FITS table)")
    parser_wave1.add_argument("input", type=str,
                              help="Input filename of 1D spectrum of flux standard star")
    parser_wave1.add_argument("pixtable", type=str,
                              help="Pixeltable of line identification from 'PyNOT-identify'")
    parser_wave1.add_argument("-o", "--output", type=str, required=True,
                              help="Output filename of wavelength calibrated 1D spectrum (FITS table)")
    parser_wave1.add_argument("--order", type=int, default=5,
                              help="Polynomial order for fitting wavelength solution")
    parser_wave1.add_argument("--log", action='store_true',
                              help="Create logarithmically binned spectrum")
    parser_wave1.add_argument("--npix", type=int, default=None,
                              help="Number of pixels in output spectrum  (default= number of input pixels)")
    parser_wave1.add_argument("--no-int", action='store_false',
                              help="Do not interpolate data onto linearized grid!")


    # -- wave2d :: Rectify and Wavelength Calibrate 2D Image
    parser_wave2 = recipes.add_parser('wave2d',
                                      help="Rectify 2D image and apply wavelength calibration")
    parser_wave2.add_argument("input", type=str,
                              help="Input filename of 1D spectrum of flux standard star")
    parser_wave2.add_argument("arc", type=str,
                              help="Input filename of arc line image")
    parser_wave2.add_argument("--table", type=str, required=True,
                              help="Pixeltable of line identification from 'PyNOT-identify'")
    parser_wave2.add_argument("-o", "--output", type=str, required=True,
                              help="Output filename of rectified, wavelength calibrated 2D image")
    parser_wave2.add_argument("--axis", type=int, default=2,
                              help="Dispersion axis: 1 horizontal, 2: vertical")
    parser_wave2.add_argument("--order_wl", type=int, default=5,
                              help="Order of Chebyshev polynomium for wavelength solution")
    for key, val in parameters['rectify'].items():
        parser_wave2.add_argument("--%s" % key, type=type(val), default=val)


    # -- skysub :: Sky Subtraction of 2D Image
    parser_sky = recipes.add_parser('skysub',
                                    help="Sky subtraction of 2D image")
    parser_sky.add_argument("input", type=str,
                            help="Input filename of 2D frame")
    parser_sky.add_argument("-o", "--output", type=str, required=True,
                            help="Output filename of sky-subtracted 2D image")
    parser_sky.add_argument("--axis", type=int, default=2,
                            help="Dispersion axis: 1 horizontal, 2: vertical")
    for key, val in parameters['skysub'].items():
        parser_sky.add_argument("--%s" % key, type=type(val), default=val)


    # -- crr :: Cosmic Ray Rejection and Correction
    parser_crr = recipes.add_parser('crr',
                                    help="Identification and correction of Cosmic Ray Hits")
    parser_crr.add_argument("input", type=str,
                            help="Input filename of 2D spectrum")
    parser_crr.add_argument("-o", "--output", type=str, required=True,
                            help="Output filename of cleaned image")
    parser_crr.add_argument('-n', "--niter", type=int, default=4,
                            help="Dispersion axis: 1 horizontal, 2: vertical")
    parser_crr.add_argument('-g', "--gain", type=float, default=0.16,
                            help="Detector gain, default for ALFOSC CCD14: 0.16 e-/ADU")
    parser_crr.add_argument('-r', "--rdnoise", type=float, default=4.3,
                            help="Detector read noise, default for ALFOSC CCD14: 4.3 e-")


    # -- flux1d :: Flux calibration of 1D spectrum
    parser_flux1 = recipes.add_parser('flux1d',
                                      help="Flux calibration of 1D spectrum")
    parser_flux1.add_argument("input", type=str,
                              help="Input filename of 1D spectrum")
    parser_flux1.add_argument("response", type=str,
                              help="Reference response function from 'PyNOT-response'")
    parser_flux1.add_argument("-o", "--output", type=str, required=True,
                              help="Output filename of flux-calibrated spectrum")


    # -- flux2d :: Flux calibration of 2D spectrum
    parser_flux2 = recipes.add_parser('flux2d',
                                      help="Flux calibration of 2D spectrum")
    parser_flux2.add_argument("input", type=str,
                              help="Input filename of 2D spectrum")
    parser_flux2.add_argument("response", type=str,
                              help="Reference response function from 'PyNOT-response'")
    parser_flux2.add_argument("-o", "--output", type=str, required=True,
                              help="Output filename of flux-calibrated 2D spectrum")


    # -- extract :: Extraction of 1D spectrum from 2D
    parser_ext = recipes.add_parser('extract',
                                    help="Extract 1D spectrum from 2D")
    parser_ext.add_argument("input", type=str,
                            help="Input filename of 2D spectrum")
    parser_ext.add_argument("-o", "--output", type=str, required=True,
                            help="Output filename of 1D spectrum (FITS Table)")
    parser_ext.add_argument("--axis", type=int, default=1,
                            help="Dispersion axis: 1 horizontal, 2: vertical")
    parser_ext.add_argument('-i', "--interactive", action='store_true',
                            help="Dispersion axis: 1 horizontal, 2: vertical")
    parameters['extract'].pop('interactive')
    for key, val in parameters['extract'].items():
        parser_ext.add_argument("--%s" % key, type=type(val), default=val)


    # -- classify :: Classify ALFOSC Files
    parser_class = recipes.add_parser('classify',
                                      help="Classify ALFOSC files")
    parser_class.add_argument("path", type=str, nargs='+',
                              help="Path (or paths) to raw ALFOSC data to be classified")
    parser_class.add_argument("-o", "--output", type=str, required=True,
                              help="Filename of file classification table (*.pfc)")
    parser_class.add_argument("-v", "--verbose", action='store_true',
                              help="Print status messages to terminal")


    # Spectral Redux:
    parser_redux = recipes.add_parser('spec',
                                      help="Combine imaging flat frames")
    parser_redux.add_argument("options", type=str,
                              help="Input filename of pipeline configuration in YAML format")
    parser_redux.add_argument("-v", "--verbose", action="store_true",
                              help="Print log to terminal")
    parser_redux.add_argument("-i", "--interactive", action="store_true",
                              help="Use interactive interface throughout")

    args = parser.parse_args()


    # -- Define Workflow
    recipe = args.recipe
    log = ['']
    if recipe == 'spec':
        main(options_fname=args.options, verbose=args.verbose, interactive=args.interactive)

    elif recipe == 'bias':
        from calibs import combine_bias_frames
        print("Running task: Bias combination")
        input_list = np.loadtxt(args.input, dtype=str)
        log = combine_bias_frames(input_list, args.output, kappa=args.kappa)

    elif recipe == 'sflat':
        from calibs import combine_flat_frames, normalize_spectral_flat
        print("Running task: Spectral flat field combination and normalization")
        flatcombine, log = combine_flat_frames(args.input, output='', mbias=args.mbias, mode='spec',
                                               dispaxis=args.dispaxis, kappa=args.kappa)

        _, log = normalize_spectral_flat(flatcombine, args.output, dispaxis=args.axis,
                                         lower=args.lower, upper=args.upper, order=args.order,
                                         sigma=args.sigma, show=False)

    elif recipe == 'corr':
        from scired import correct_raw_file
        print("Running task: Bias subtraction and flat field correction")
        log = correct_raw_file(args.input, output=args.output, bias_fname=args.bias, flat_fname=args.flat,
                               overscan=50, overwrite=True)

    elif recipe == 'identify':
        from PyQt5 import QtWidgets
        from identify_gui import GraphicInterface
        # Launch App:
        app = QtWidgets.QApplication(sys.argv)
        gui = GraphicInterface(args.arc,
                               linelist_fname=args.lines,
                               dispaxis=args.axis)
        gui.show()
        app.exit(app.exec_())

    elif recipe == 'response':
        from PyQt5 import QtWidgets
        from response_gui import ResponseGUI
        # Launch App:
        app = QtWidgets.QApplication(sys.argv)
        gui = ResponseGUI(args.input, output_fname=args.output)
        gui.show()
        app.exit(app.exec_())

    elif recipe == 'wave1d':
        from wavecal import wavecal_1d
        print("Running task: 1D Wavelength Calibration")
        log = wavecal_1d(args.input, args.pixtable, output=args.output, order_wl=args.order,
                         log=args.log, N_out=args.npix, linearize=args.no_int)

    elif recipe == 'wave2d':
        from wavecal import rectify
        print("Running task: 2D Rectification and Wavelength Calibration")
        options = copy(vars(args))
        vars_to_remove = ['recipe', 'input', 'arc', 'table', 'output', 'dir', 'axis']
        for varname in vars_to_remove:
            options.pop(varname)
        log = rectify(args.input, args.arc, args.table, output=args.output, fig_dir=args.dir,
                      dispaxis=args.axis, **options)

    elif recipe == 'skysub':
        from scired import auto_fit_background
        print("Running task: Background Subtraction")
        options = copy(vars(args))
        vars_to_remove = ['recipe', 'input', 'output', 'axis']
        for varname in vars_to_remove:
            options.pop(varname)
        log = auto_fit_background(args.input, args.output, dispaxis=args.axis,
                                  plot_fname="skysub_diagnostics.pdf",
                                  **options)

    elif recipe == 'crr':
        from scired import correct_cosmics
        print("Running task: Cosmic Ray Rejection")
        log = correct_cosmics(args.input, args.output, niter=args.niter,
                              gain=args.gain, readnoise=args.rdnoise)

    elif recipe == 'flux1d':
        from response import flux_calibrate_1d
        print("Running task: Flux Calibration of 1D Spectrum")
        log = flux_calibrate_1d(args.input, output=args.output, response=args.response)

    elif recipe == 'flux2d':
        from response import flux_calibrate
        print("Running task: Flux Calibration of 2D Image")
        log = flux_calibrate(args.input, output=args.output, response=args.response)

    elif recipe == 'extract':
        from PyQt5 import QtWidgets
        from extract_gui import ExtractGUI
        options = copy(vars(args))
        vars_to_remove = ['recipe', 'input', 'output', 'axis']
        for varname in vars_to_remove:
            options.pop(varname)

        if args.interactive:
            app = QtWidgets.QApplication(sys.argv)
            gui = ExtractGUI(args.input, output_fname=args.output, dispaxis=args.axis, **options)
            gui.show()
            app.exit(app.exec_())
        else:
            from extraction import auto_extract
            print("Running task: 1D Extraction")
            log = auto_extract(args.input, args.output, dispaxis=args.dispaxis,
                               pdf_fname="extract_diagnostics.pdf", **options)

    elif recipe == 'classify':
        import data_organizer as do
        # Classify files:
        print("Classyfying files...")
        database, message = do.classify(args.path, progress=args.verbose)
        do.io.save_database(database, args.output)
        log = message
        log += "\nSaved file classification database: %s" % args.output

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

    # output_message = "\n".join(log)
    # print(output_message)
    print(log)


if __name__ == '__main__':
    main()
