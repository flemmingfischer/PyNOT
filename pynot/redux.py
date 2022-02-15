"""
Automatically Classify and Reduce a given Data Set
"""

from astropy.io import fits
from collections import defaultdict
import glob
import numpy as np
import os
import sys

from pynot import instrument
from pynot.data import io
from pynot.data import organizer as do
from pynot.data import obs
from pynot.calibs import task_bias, task_sflat, task_prep_arcs
from pynot.extraction import auto_extract
from pynot import extract_gui
from pynot.functions import get_options, get_version_number
from pynot.wavecal import rectify, WavelengthError
from pynot.identify_gui import create_pixtable
from pynot.scired import raw_correction, auto_fit_background, correct_cosmics
from pynot.scombine import combine_2d
from pynot.response import flux_calibrate, task_response
from pynot.logging import Report
from PyQt5.QtWidgets import QApplication

code_dir = os.path.dirname(os.path.abspath(__file__))
calib_dir = os.path.join(code_dir, 'calib/')
defaults_fname = os.path.join(calib_dir, 'default_options.yml')
__version__ = get_version_number()


class State(dict):
    """A collection of variables for the pipeline, such as arc line ID tables etc."""
    def __init__(self):
        dict.__init__(self, {})
        self.current = ''

    def print_current_state(self):
        print(self.current)

    def set_current_state(self, state):
        self.current = state

    def find_pixtab(self, grism, basename=None):
        """Find a pixel-table that matches the `grism` and arc `basename` (optional)"""
        matches = list()
        for key, fname in self.items():
            if grism in key:
                if basename is None:
                    matches.append(fname)
                else:
                    if basename in key:
                        matches.append(fname)
        return matches


def run_pipeline(options_fname, object_id=None, verbose=False, interactive=False, force_restart=False,
                 make_bias=False, make_flat=False, make_arcs=False, make_response=False):
    log = Report(verbose)
    status = State()

    global app
    app = QApplication(sys.argv)

    # -- Parse Options from YAML
    options = get_options(defaults_fname)

    user_options = get_options(options_fname)
    for section_name, section in user_options.items():
        if isinstance(section, dict):
            options[section_name].update(section)
        else:
            options[section_name] = section

    if object_id is None:
        pass
    elif isinstance(object_id, str):
        object_id = [object_id]
    elif isinstance(object_id, list):
        if isinstance(object_id[0], str):
            pass
        else:
            log.error("Wrong input type for `object id`. Must be list of strings")
            log.error("not list of %r" % type(object_id[0]))
            log.fatal_error()
            return
    else:
        log.error("Wrong input type for `object id`. Must be string or list of strings")
        log.error("not %r" % type(object_id))
        log.fatal_error()
        return

    if interactive:
        # Set all interactive steps to True
        options['identify']['interactive'] = True
        options['identify']['all'] = True
        options['extract']['interactive'] = True
        options['response']['interactive'] = True

    dataset_fname = options['dataset']
    if dataset_fname and os.path.exists(dataset_fname):
        # -- load collection
        database = io.load_database(dataset_fname)
        log.write("Loaded file classification database: %s" % dataset_fname)
    else:
        log.error("Dataset does not exist : %s" % dataset_fname)
        log.fatal_error()
        return

    # -- Organize object files in dataset:
    if 'SPEC_OBJECT' not in database:
        log.error("No spectroscopic data found in the dataset!")
        log.error("Check the classification table... object type 'SPEC_OBJECT' missing")
        log.fatal_error()
        return
    object_filelist = database['SPEC_OBJECT']
    try:
        object_images = list(map(do.RawImage, object_filelist))
    except (ValueError, do.UnknownObservingMode, OSError, FileNotFoundError, TypeError, IndexError) as e:
        log.error(str(e))
        log.fatal_error()
        raise

    log.add_linebreak()
    log.write(" - The following objects were found in the dataset:", prefix='')
    log.write("      OBJECT           GRISM        SLIT      EXPTIME       FILENAME", prefix='')
    for sci_img in object_images:
        output_variables = (sci_img.object, sci_img.grism, sci_img.slit, sci_img.exptime, os.path.basename(sci_img.filename))
        log.write("%20s  %9s  %11s   %5.0f  %s" % output_variables, prefix='')
    log.add_linebreak()


    # Start Calibration Tasks:
    output_base = obs.output_base_spec
    if not os.path.exists(os.path.join(output_base, 'arcs')):
        os.makedirs(os.path.join(output_base, 'arcs'))
    if not os.path.exists(os.path.join(output_base, 'std')):
        os.makedirs(os.path.join(output_base, 'std'))


    # -- bias
    if not database.has_tag('MBIAS') or make_bias:
        task_output, log = task_bias(options['bias'], database=database, log=log, verbose=verbose, output_dir=output_base)
        for tag, filelist in task_output.items():
            database[tag] = filelist
        io.save_database(database, dataset_fname)
    else:
        log.write("Static calibrations for master bias already exist. Skipping")


    # -- sflat
    if not database.has_tag('NORM_SFLAT') or make_flat:
        task_output, log = task_sflat(options['flat'], database=database, log=log, verbose=verbose, output_dir=output_base)
        for tag, filelist in task_output.items():
            database[tag] = filelist
        io.save_database(database, dataset_fname)
    else:
        log.write("Static calibrations for normalized flats already exist. Skipping")


    # -- Check arc line files:
    if not database.has_tag('ARC_CORR') or make_arcs:
        database.pop('ARC_CORR', None)
        task_output, log = task_prep_arcs(options, database, log=log, verbose=verbose,
                                          output_dir=os.path.join(output_base, 'arcs'))
        for tag, arc_images in task_output.items():
            database[tag] = arc_images
        io.save_database(database, dataset_fname)
    else:
        log.write("Corrected arc lamp frames already exist. Skipping")
        arc_images = database['ARC_CORR']


    # -- initial identify
    # get list of unique grisms in dataset:
    grism_list = list()
    for sci_img in object_images:
        grism_name = sci_img.grism
        if grism_name not in grism_list:
            grism_list.append(grism_name)

    arc_images_for_grism = defaultdict(list)
    for arc_fname in arc_images:
        this_grism = instrument.get_grism(fits.getheader(arc_fname))
        arc_images_for_grism[this_grism].append(arc_fname)

    for grism_name in grism_list:
        pixtab_fname = os.path.join(calib_dir, '%s_pixeltable.dat' % grism_name)
        if os.path.exists(pixtab_fname):
            continue

        log.write("Starting interactive definition of pixel table for %s" % grism_name)
        try:
            arc_fname = arc_images_for_grism[grism_name][0]
            pixtab_fname = os.path.join(calib_dir, '%s_pixeltable.dat' % grism_name)
            linelist_fname = ''
            log.write("Input arc frame: %s" % arc_fname)

            arc_base_fname = os.path.basename(arc_fname)
            arc_base, ext = os.path.splitext(arc_base_fname)
            output_pixtable = os.path.join(output_base, 'arcs', "pixtab_%s_%s.dat" % (arc_base, grism_name))
            poly_order, saved_pixtab_fname, msg = create_pixtable(arc_fname, grism_name, output_pixtable,
                                                                  pixtab_fname, linelist_fname,
                                                                  order_wl=options['identify']['order_wl'],
                                                                  app=app)
            status["pixtab_%s_%s" % (arc_base, grism_name)] = output_pixtable
            log.commit(msg)
        except:
            log.error("Identification of arc lines failed!")
            log.fatal_error()
            log.save()
            print("Unexpected error:", sys.exc_info()[0])
            raise

    identify_all = options['identify']['all']
    # update status with all available pixtables:
    local_pixtables = glob.glob(os.path.join(output_base, 'arcs', "pixtab_*.dat"))
    for fname in local_pixtables:
        pixtab_id = os.path.splitext(fname)[0].split('_')[1]
        status[pixtab_id] = fname
    cached_pixtables = glob.glob(os.path.join(calib_dir, "*_pixeltable.dat"))
    for fname in cached_pixtables:
        pixtab_id = fname.split('_')[0]
        status[pixtab_id] = fname


    # -- response
    if not database.has_tag('RESPONSE') or make_response:
        if database.has_tag('SPEC_FLUX-STD'):
            task_output, log = task_response(options, database, status, log=log, verbose=verbose, app=app,
                                             output_dir=os.path.join(output_base, 'std'))
        else:
            task_output = {}
            log.warn("No data for file type: SPEC_FLUX-STD")
            log.warn("Could not determine instrument response function.")
            log.warn("Spectra will not be flux-calibrated")
        for tag, response_files in task_output.items():
            database[tag] = response_files
        io.save_database(database, dataset_fname)
    else:
        log.write("Static calibrations for response functions already exist. Skipping")


    # Save overview log:
    print("")
    print("          - Pipeline setup ended successfully.")
    print("            Consult the overview log: %s\n\n" % log.fname)
    log.save()


    if any([make_bias, make_flat, make_arcs, make_response]):
        print("          - Static Calibrations Finished.")
        return

    # ------------------------------------------------------------------
    # -- Start Main Reduction:
    if object_id is None:
        # Loop over all:
        objects_to_reduce = object_images
    else:
        objects_to_reduce = list()
        for img in object_images:
            if img.object in object_id:
                objects_to_reduce.append(img)

        if len(objects_to_reduce) == 0:
            log.error("No data matched the given object ID: %r" % object_id)
            log.fatal_error()
            return
        else:
            force_restart = True

    # Organize the science files according to target and instrument setup (insID)
    science_frames = defaultdict(lambda: defaultdict(list))
    for sci_img in objects_to_reduce:
        filt_name = sci_img.filter
        insID = "%s_%s" % (sci_img.grism, sci_img.slit.replace('_', ''))
        if filt_name.lower() not in ['free', 'open', 'none']:
            insID = "%s_%s" % (insID, filt_name)
        science_frames[sci_img.target_name][insID].append(sci_img)

    obd_fname = os.path.splitext(dataset_fname)[0] + '.obd'
    obdb = obs.OBDatabase(obd_fname)
    if os.path.exists(obd_fname):
        log.write("Loaded OB database: %s" % obd_fname)
    else:
        log.write("Initiated OB database: %s" % obd_fname)
    obdb.update_spectra(science_frames)
    log.write("Updating OB database")
    log.add_linebreak()

    for target_name, frames_per_setup in science_frames.items():
        for insID, frames in frames_per_setup.items():
            for obnum, sci_img in enumerate(frames, 1):
                # Create working directory:
                obID = 'ob%i' % obnum
                output_dir = os.path.join(output_base, sci_img.target_name, insID, obID)
                if obdb.data[output_dir] in ['DONE', 'SKIP']:
                    if force_restart and obdb.data[output_dir] == 'DONE':
                        pass
                    else:
                        log.write("Skipping OB: %s  (status=%s)" % (output_dir, obdb.data[output_dir]))
                        log.write("Change OB status to blank in the .obd file if you want to redo the reduction")
                        log.write("or run the pipeline with the '-f' option to force re-reduction of all OBs")
                        log.add_linebreak()
                        continue
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)

                # Start new log in working directory:
                log_fname = os.path.join(output_dir, 'pynot.log')
                log.clear()
                log.set_filename(log_fname)
                log.write("------------------------------------------------------------", prefix='')
                log.write("Starting PyNOT Longslit Spectroscopic Reduction")
                log.add_linebreak()
                log.write("Target Name: %s" % sci_img.target_name)
                log.write("Input Filename: %s" % sci_img.filename)
                log.write("Grism: %s" % sci_img.grism)
                log.write("Saving output to directory: %s" % output_dir)
                log.add_linebreak()

                # Prepare output filenames:
                grism = sci_img.grism
                rect2d_fname = os.path.join(output_dir, 'RECT2D_%s.fits' % (sci_img.target_name))
                bgsub2d_fname = os.path.join(output_dir, 'SKYSUB2D_%s.fits' % (sci_img.target_name))
                corrected_2d_fname = os.path.join(output_dir, 'CORR2D_%s.fits' % (sci_img.target_name))
                flux2d_fname = os.path.join(output_dir, 'FLUX2D_%s.fits' % (sci_img.target_name))
                flux1d_fname = os.path.join(output_dir, 'FLUX1D_%s.fits' % (sci_img.target_name))
                extract_pdf_fname = os.path.join(output_dir, 'extraction_details.pdf')

                # Find Bias Frame:
                try:
                    master_bias_fname = do.match_single_calib(sci_img, database, 'MBIAS', log, date=False)
                except Exception:
                    log.fatal_error()
                    raise

                # Find Flat Frame:
                try:
                    norm_flat_fname = do.match_single_calib(sci_img, database, 'NORM_SFLAT', log, date=False,
                                                            grism=True, slit=True, filter=True)
                except Exception:
                    log.fatal_error()
                    raise

                # Find Arc Frame:
                try:
                    arc_fname = do.match_single_calib(sci_img, database, 'ARC_CORR', log, date=False,
                                                      grism=True, slit=True, get_closest_time=True)
                except Exception:
                    log.fatal_error()
                    raise

                arc_base = os.path.splitext(os.path.basename(arc_fname))[0]
                pixtable_fname = os.path.join(output_base, 'arcs', "pixtab_%s_%s.dat" % (arc_base, grism))
                if os.path.exists(pixtable_fname):
                    pixtable = pixtable_fname
                elif identify_all:
                    log.write("Running task: Arc Line Identification")
                    try:
                        linelist_fname = ''
                        pixtab_fname = os.path.join(calib_dir, '%s_pixeltable.dat' % grism)
                        output_pixtable_fname = os.path.join(output_base, 'arcs', "pixtab_%s_%s.dat" % (arc_base, grism))
                        order_wl, pixtable, msg = create_pixtable(arc_fname, grism,
                                                                  output_pixtable_fname,
                                                                  pixtab_fname, linelist_fname,
                                                                  order_wl=options['identify']['order_wl'],
                                                                  app=app)
                        status["pixtab_%s_%s" % (arc_base, grism)] = pixtable
                        log.commit(msg)
                        log.add_linebreak()
                    except Exception:
                        log.error("Identification of arc lines failed!")
                        log.fatal_error()
                        print("Unexpected error:", sys.exc_info()[0])
                        raise
                else:
                    pixtab_fnames = status.find_pixtab(grism)
                    pixtable = pixtab_fnames[0]


                # Bias correction, Flat correction
                log.write("Running task: Bias and Flat Field Correction")
                try:
                    output_msg = raw_correction(sci_img.data, sci_img.header, master_bias_fname, norm_flat_fname,
                                                output=corrected_2d_fname, overwrite=True)
                    log.commit(output_msg)
                    log.add_linebreak()
                except Exception:
                    log.error("Bias and flat field correction failed!")
                    log.fatal_error()
                    print("Unexpected error:", sys.exc_info()[0])
                    raise


                # Call rectify
                log.write("Running task: 2D Rectification and Wavelength Calibration")
                try:
                    rect_msg = rectify(corrected_2d_fname, arc_fname, pixtable,
                                       output=rect2d_fname, fig_dir=output_dir,
                                       dispaxis=sci_img.dispaxis, **options['rectify'])
                    log.commit(rect_msg)
                    log.add_linebreak()
                except WavelengthError:
                    log.error("2D rectification failed!")
                    log.fatal_error()
                    print("Unexpected error:", sys.exc_info()[0])
                    print("")
                    raise


                # Automatic Background Subtraction:
                if options['skysub']['auto']:
                    bgsub_pdf_name = os.path.join(output_dir, 'skysub_diagnostics.pdf')
                    log.write("Running task: Background Subtraction")
                    try:
                        bg_msg = auto_fit_background(rect2d_fname, bgsub2d_fname, dispaxis=1,
                                                     plot_fname=bgsub_pdf_name, **options['skysub'])
                        log.commit(bg_msg)
                        log.write("2D sky model is saved in extension 'SKY' of the file: %s" % bgsub2d_fname)
                        log.add_linebreak()
                    except Exception:
                        log.error("Automatic background subtraction failed!")
                        log.fatal_error()
                        print("Unexpected error:", sys.exc_info()[0])
                        raise
                else:
                    log.warn("No sky-subtraction has been performed on the 2D spectrum!")
                    log.write("Cosmic ray rejection may fail... double check the output or turn off 'crr' by setting niter=0.")
                    log.add_linebreak()
                    bgsub2d_fname = rect2d_fname


                # Correct Cosmic Rays Hits:
                if options['crr']['niter'] > 0:
                    log.write("Running task: Cosmic Ray Rejection")
                    crr_fname = os.path.join(output_dir, 'CRR_SKYSUB2D_%s.fits' % (sci_img.target_name))
                    try:
                        crr_msg = correct_cosmics(bgsub2d_fname, crr_fname, **options['crr'])
                        log.commit(crr_msg)
                        log.add_linebreak()
                    except Exception:
                        log.error("Cosmic ray correction failed!")
                        log.fatal_error()
                        print("Unexpected error:", sys.exc_info()[0])
                        raise
                else:
                    crr_fname = bgsub2d_fname


                # Flux Calibration:
                if database.has_tag('RESPONSE'):
                    response_fname = do.match_response(sci_img, database['RESPONSE'], exact_date=False)
                else:
                    response_fname = ''

                if response_fname:
                    log.write("Running task: Flux Calibration")
                    try:
                        flux_msg = flux_calibrate(crr_fname, output=flux2d_fname, response_fname=response_fname)
                        log.commit(flux_msg)
                        log.add_linebreak()
                        status['FLUX2D'] = flux2d_fname
                    except Exception:
                        log.error("Flux calibration failed!")
                        log.fatal_error()
                        print("Unexpected error:", sys.exc_info()[0])
                        raise
                else:
                    log.warn("Could not find a response function that matches the observations!")
                    log.warn("The spectra will not be flux clibrated!")
                    status['FLUX2D'] = crr_fname


                # Extract 1D spectrum:
                log.write("Running task: 1D Extraction")
                extract_fname = status['FLUX2D']
                if options['extract']['interactive']:
                    try:
                        log.write("Extraction: Starting Graphical User Interface")
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
                        ext_msg = auto_extract(extract_fname, flux1d_fname,
                                               dispaxis=1, pdf_fname=extract_pdf_fname,
                                               **options['extract'])
                        log.commit(ext_msg)
                        log.add_linebreak()
                    except np.linalg.LinAlgError:
                        log.warn("Automatic extraction failed. Try manual extraction...")
                    except Exception:
                        log.error("Spectral 1D extraction failed!")
                        log.fatal_error()
                        print("Unexpected error:", sys.exc_info()[0])
                        raise

                obdb.update(output_dir, 'DONE')
                log.exit()



            # -- Combine OBs for same target:

            # Check whether to combine or link OB files:
            pattern = os.path.join(output_base, target_name, insID, '*', 'FLUX2D*.fits')
            files_to_combine = glob.glob(pattern)
            files_to_combine = list(filter(lambda x: obdb.data[os.path.dirname(x)] == 'DONE', files_to_combine))
            if len(files_to_combine) > 1:
                # Combine individual OBs
                comb_basename = '%s_%s_flux2d.fits' % (target_name, insID)
                comb2d_fname = os.path.join(output_base, target_name, comb_basename)
                if not os.path.exists(comb2d_fname) or force_restart:
                    log.write("Running task: Spectral Combination")
                    try:
                        comb_output = combine_2d(files_to_combine, comb2d_fname)
                        final_wl, final_flux, final_err, final_mask, output_msg = comb_output
                        log.commit(output_msg)
                        log.add_linebreak()
                    except Exception:
                        log.warn("Combination of 2D spectra failed... Try again manually")
                        raise

                comb_basename = '%s_%s_flux1d.fits' % (target_name, insID)
                comb1d_fname = os.path.join(output_base, target_name, comb_basename)
                if not os.path.exists(comb1d_fname) or force_restart:
                    log.add_linebreak()
                    log.write("Running task: 1D Extraction")
                    if options['extract']['interactive']:
                        try:
                            log.write("Extraction: Starting Graphical User Interface")
                            extract_gui.run_gui(comb2d_fname, output_fname=comb1d_fname,
                                                app=app, **options['extract'])
                            log.write("Writing fits table: %s" % comb1d_fname, prefix=" [OUTPUT] - ")
                        except:
                            log.error("Interactive 1D extraction failed!")
                            log.fatal_error()
                            print("Unexpected error:", sys.exc_info()[0])
                            raise
                    else:
                        try:
                            pdf_basename = 'comb_%s_extraction_details.pdf' % insID
                            extract_pdf_fname = os.path.join(output_base, target_name, pdf_basename)
                            ext_msg = auto_extract(comb2d_fname, comb1d_fname,
                                                   dispaxis=1, pdf_fname=extract_pdf_fname,
                                                   **options['extract'])
                            log.commit(ext_msg)
                            log.add_linebreak()
                        except Exception:
                            log.warn("Automatic extraction failed. Try manual extraction...")

            elif len(files_to_combine) == 1:
                # Create a hard link to the individual file instead
                comb_basename = '%s_%s_flux2d.fits' % (target_name, insID)
                comb2d_fname = os.path.join(output_base, target_name, comb_basename)
                source_2d = files_to_combine[0]
                if os.path.exists(comb2d_fname):
                    os.remove(comb2d_fname)
                os.link(source_2d, comb2d_fname)
                log.write("Created file link:")
                log.write("%s -> %s" % (source_2d, comb2d_fname), prefix=" [OUTPUT] - ")

                comb_basename = '%s_%s_flux1d.fits' % (target_name, insID)
                comb1d_fname = os.path.join(output_base, target_name, comb_basename)
                source_1d = source_2d.replace('FLUX2D', 'FLUX1D')
                if os.path.exists(comb1d_fname):
                    os.remove(comb1d_fname)
                os.link(source_1d, comb1d_fname)
                log.write("Created file link:")
                log.write("%s -> %s" % (source_1d, comb1d_fname), prefix=" [OUTPUT] - ")
                log.add_linebreak()
