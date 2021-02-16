# PyNOT-redux
 A Data Processing Pipeline for ALFOSC at the Nordic Optical Telescope


PyNOT handles long-slit spectroscopic data (an extension for imaging data is currently being developed). The pipeline is implemented entirely in Python and can be run directly from the terminal. The main workflow is mostly automated (and can in fact be run fully automated) and includes a graphical user interface for certain tasks (such as line identification for wavelength calibration and spectral 1D extraction).

A special thank you goes out to Prof. Johan Fynbo for helpful discussions and feedback, and for introducing me to the Nordic Optical Telescope in the first place (back in 2012).

```diff
- The pipeline is currently in a testing stage!
  Feel free to test it on your own data and let me know if you find any issues.
  I'll respond as fast as possible.
```

## Installation
The pipeline can be installed using [pip](https://www.pypi.org):

    ]% pip install PyNOT-redux

and requires the following packages : `astroalign`, `astropy`, `astroscrappy`, `lmfit`, `matplotlib`, `numpy`, `PyQt5`, `PyYAML`, `scipy`, `sep`, and `spectres`. I want to give a huge shout out to all the developers of these packages. Thanks for sharing your work!


## Basic Usage
The pipeline is implemented as a series of modules or "recipes" that can either be executed individually or as a fully assembled pipeline. The available recipes can be shown by running:

    ]% pynot -h

and the input parameters for each recipe can be inspected by running:

    ]% pynot  recipe-name  -h

Three of the recipes have slightly special behavior:

 - `init` : initiates a default parameter file in YAML format.

 - `classify` : runs the data organizer that creates a file classification report of all the files in the given directory. This is used as part of the pipeline to identify the necessary files for each step. Files can be ignored by commenting them out.

 - `spex` : runs the full spectroscopic pipeline using the parameter file generated by `pynot init`. The full pipeline performs wavelength calibration and rectifies the 2D spectrum, subtracts the sky background, corrects cosmic ray hits, flux calibrates the 2D spectrum and performs an automated optimal extraction of all objects identified in the slit.

The extracted 1D spectra are saved as a multi-extension FITS file where each object identified in the slit has its own extension:

    No.    Name      Ver    Type      Cards   Dimensions   Format
      0  PRIMARY       1 PrimaryHDU       4   ()      
      1  OBJ1          1 BinTableHDU    158   1026R x 3C   [D, D, D]
      2  OBJ2          1 BinTableHDU    158   1026R x 3C   [D, D, D]
      :    :           :     :           :         :           :    
      :    :           :     :           :         :           :    

Each spectrum is saved as a Binary Table with three columns 'WAVE', 'FLUX', and 'ERR'. The header of each extension contains the information about the original image such as exposure time and instrument settings.


## Documentation

The full documentation is currently being compiled... stay tuned.


## Examples

#### Spectroscopy
A standard example would be the reduction of the data from one night of observations. All the raw data would be located in a single folder - let's call it `raw_data/`. This folder will contain the necessary raw data: bias frames, flux standard star spectra, arc line frames, spectroscopic flat fields, and the object spectra. Any other data in the folder (imaging files, sky flats, acquisition images, slit images etc.) will be ignored in the pipeline.

A default reduction would require the following steps:

1. Classify the data:
    `pynot classify raw_data --output night1.pfc`

  This step creates the PyNOT File Classification (.pfc) table which looks something like:

        # PyNOT File Classification Table

        # ARC_HeNe:
        #FILENAME             TYPE      OBJECT     EXPTIME  GRISM     SLIT      FILTER
         raw/ALzh010234.fits  ARC_HeNe  HeNe           3.0  Grism_#4  Slit_1.3  Open
         raw/ALzh010235.fits  ARC_HeNe  HeNe           3.0  Grism_#4  Slit_1.3  Open
         raw/ALzh010247.fits  ARC_HeNe  HeNe           3.0  Grism_#4  Slit_1.0  Open
         raw/ALzh010250.fits  ARC_HeNe  HeNe           3.0  Grism_#4  Slit_1.0  Open

        # BIAS:
        #FILENAME             TYPE  OBJECT     EXPTIME  GRISM        SLIT      FILTER
         raw/ALzh010001.fits  BIAS  bias-full  0.0  Open_(Lyot)  Open      Open
         raw/ALzh010002.fits  BIAS  bias-full  0.0  Open_(Lyot)  Open      Open
         raw/ALzh010003.fits  BIAS  bias-full  0.0  Open_(Lyot)  Open      Open

        ...

 If there are any bad frames (that you know of) you can delete or comment out (using #) the corresponding line to ignore the file in the pipeline.


2. Create a parameter file:
    `pynot init spex night1.yml`

  This will initiate a new parameter file with default values. All available parameters of the steps of the pipeline are laid out in this file. Open the file with your favorite text editor and insert the name of the PFC table under the parameter `dataset` and edit any other values as you see fit. A short description of the parameters is given in the file. For more detail, see the full documentation.

  For now we will just focus on the interactive parameters: There are three recipes that can be used in interactive mode, which will start a graphical interface to allow the user more flexibility. These are: line identification (for wavelength calibration), extraction of the 1-dimensional spectra, and calculation of the response function. By default, these are all turned on. Note that the line identification can be defined in two ways:
  (i)  once for all grisms in the given dataset, this line identification information will then automatically be used for all objects observed with the given grism;
  or (ii) for each object in the dataset based on the arc file observed closest in time to the science frame. This provides more accurate rectification of the image, but the difference in low-resolution data is usually negligible.


3. Run the pipeline:
    `pynot spex night1.yml`

  This will start the full pipeline reduction of *all* objects identified in the dataset (with file classification `SPEC_OBJECT`). If you only want to reduce a few targets, you can specify these as: `pynot spex night1.yml --object TARGET1 TARGET2 ...` where the target names must match the value of the `OBJECT` keyword in the FITS headers.

  By default the pipeline runs rather silently and creates separate output directories for each target where a detailed log file is saved. This file summarizes the steps of the pipeline and shows any warnings and output generated by the pipeline. By default, the pipeline also generates diagnostic plots of the 2D rectification, response function, sky subtraction and 1D extraction.

  If you want the log printed to the terminal as the pipeline progresses, run the pipeline with the `-v` (or `--verbose`) option.


4. Verify the various steps of the data products and make sure that everything terminated successfully. You should pay special attention to the automated sky subtraction. This can be adjusted during the interactive extraction step, if necessary.


5. Now it's time to do your scientific analysis on your newly calibrated 1D and 2D spectra. Enjoy!




### Imaging

A basic automated reduction would require the following steps:

1. Classify the data:
    `pynot classify raw_data --output night1.pfc`

  This step creates the PyNOT File Classification (.pfc) table (see above).

2. Create a parameter file:
    `pynot init phot  pars1.yml`

  This will initiate a new parameter file with default values. All available parameters of the steps of the pipeline are laid out in this file. Open the file with your favorite text editor and insert the name of the PFC table under the parameter `dataset` and edit any other values as you see fit. A short description of the parameters is given in the file. For more detail, see the full documentation.

3. Run the pipeline:
    `pynot phot pars1.yml`

  This will start the full pipeline reduction of *all* objects in *all* filters identified in the dataset (with file classification `IMG_OBJECT`).
  The processed files are structured in sub-directories from the main working directory:

    ```
    working_dir/
         |- imaging/
               |- OBJECT_1/
               |     |- B_band/
               |     |- R_band/
               |     |- combined_B.fits
               |     |- combined_R.fits
               |     |...
               |
               |- OBJECT_2/
                     |- B_band/
                     |- R_band/
                     |- V_band/
                     |- combined_B.fits
                     |- combined_R.fits
                     |- combined_V.fits
                     |...
    ```
  The individual images for each filter of each target are kept in the desginated folders under each object, and are automatically combined. The combined image is in the folder of the given object. The last step of the pipeline as of now is to run a source extraction algorithm (SEP/SExtractor) to provide a final source table with aperture fluxes, a segmentation map as well as a figure showing the identified sources in the field.
  In each designated filter folder, the pipeline also produces a file log showing which files are combined into the final image as well as some basic image statistics: an estimate of the seeing, the PSF ellipticity, and the exposure time. This file can be used as input for further refined image combinations using the recipe `pynot imcombine  filelist_OBJECT_1.txt  new_combined_R.fits`. Individual frames can be commented out in the file log in order to exclude them in subsequent combinations.


4. Verify the various steps of the data products and make sure that everything terminated successfully.


5. Now it's time to do your scientific analysis on your newly calibrated images. Enjoy!


NOTE -- Photometric calibration is not implemented yet, all reported magnitudes in the source table are instrument magnitudes!!
