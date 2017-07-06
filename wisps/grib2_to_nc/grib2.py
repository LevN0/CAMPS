#!/usr/bin/env python
import sys
import os
from subprocess import call
from subprocess import Popen
import subprocess
import pygrib
import pdb
import re
import numpy as np
from datetime import timedelta
from multiprocessing import Pool
from multiprocessing.dummy import Pool as ThreadPool
from shutil import rmtree
import time
file_dir = os.path.dirname(os.path.realpath(__file__))
relative_path = "/.."
path = os.path.abspath(file_dir + relative_path)
sys.path.insert(0, path)
from data_mgmt.Wisps_data import Wisps_data
import data_mgmt.writer as writer
import data_mgmt.Time as Time
import registry.util as yamlutil

max_lead_times = 400


def reduce_grib(control=None):
    """Reads grib2 files, changes the map projection and
    grid extent, and packages the gribs into a single grib2 file
    """
    # Init
    if control is None:
        control = yamlutil.read_grib2_control()

    wgrib2 = control['wgrib2']
    grbindex = control['grbindex']
    datadir = control['datadir']
    outpath = control['outpath']
    projection = control['projection']
    nonpcp_file = control['nonpcp_file']
    pcp_file = control['pcp_file']
    outfile_identifier = control['grib_file_identifier']
    num_processors = control['num_processors']
    remove_infile = control['remove_infile']

    # projections
    gfs47a, gfs47b, gfs47c = projection.split(" ")
    if type(datadir) is not list:
        datadir = [datadir]
    for gfs_dir in datadir:
        # Find ALL files in the datadir
        files = os.listdir(gfs_dir)

        # Just get the gfs files
        match_string = r'^...\.t\d\dz\.pgrb2\.0p25\.f\d\d\d$'
        files = filter(lambda f: re.match(match_string, f, re.I), files)

        # Only get those files that are every third projection hour
        files = filter(lambda f: int(f[-3:]) % 3 ==
                       0 and int(f[-3:]) <= max_lead_times, files)

        if len(files) > 0:
            print "number of files:", files
            forecast_hour = files[0][5:7]
            outfile_name = 'mdl.gfs47.' + \
                forecast_hour + "." + \
                outfile_identifier + '.pgrb2'
            outfile = outpath + outfile_name

            tmp_dir = outpath + 'tmp/'
        if os.path.exists(tmp_dir):
            rmtree(tmp_dir)
        os.makedirs(tmp_dir)
        # Loop through files in directory
        commands = []
        tmp_outfiles = []
        for gfs_file in files:
            infile = gfs_dir + gfs_file
            print infile
            tmp_outfile = tmp_dir + os.path.basename(infile)
            tmp_outfiles.append(tmp_outfile)
            # print tmp_outfile

            PIPE = subprocess.PIPE
            get_inv_cmd = [wgrib2, infile]
            non_pcp_cmd = [wgrib2, infile,  # Specify infile
                           # Use Inventory
                           "-i",
                           # new_grid wind orientation ('grid' or 'earth')
                           "-new_grid_winds", "grid",
                           # new_grid interpolation:
                           # ('bilinear','bicubic','neighbor', or 'budget')
                           "-new_grid_interpolation", "bilinear",
                           # add to existing file if exist
                           "-append",
                           # new_grid to specifications
                           "-new_grid", gfs47a, gfs47b, gfs47c,
                           tmp_outfile]

            pcp_cmd = [wgrib2, infile,  # Specify infile
                       # Use Inventory
                       "-i",
                       # new_grid wind orientation ('grid' or 'earth')
                       "-new_grid_winds", "grid",
                       # new_grid interpolation:
                       # ('bilinear','bicubic','neighbor', or 'budget')
                       "-new_grid_interpolation", "budget",
                       # add to existing file if exist
                       "-append",
                       # new_grid to specifications
                       "-new_grid", gfs47a, gfs47b, gfs47c,
                       tmp_outfile]

        # p1 = Popen(get_inv_cmd, stdout=PIPE)
        # p2 = Popen(['grep','-f', nonpcp_file],stdin=p1.stdout,
        #       stdout=PIPE)
        # p3 = Popen(non_pcp_cmd, stdin=p2.stdout)
        # p1.stdout.close()
        # p2.stdout.close()

            # OR

            # nonPCP
            npcp_cmd = " ".join(get_inv_cmd) + " | grep -f " + \
                nonpcp_file + " | " + " ".join(non_pcp_cmd)
            # PCP
            pcp_cmd = " ".join(get_inv_cmd) + " | grep -f " + \
                pcp_file + " | " + " ".join(pcp_cmd)

            commands.append((npcp_cmd, pcp_cmd))
        print "starting multiprocessing on", num_processors, " cores."
        start = time.time()
        pool = Pool(num_processors)
        ret = pool.map(process_commands, commands)
        pool.close()
        pool.join()
        end = time.time()
        print "elepsed time: " + str((end - start))
        for reduced_file in tmp_outfiles:
            cmd = " ".join([wgrib2, reduced_file, "-append", "-grib", outfile])
            subprocess.check_output(cmd, shell=True)

    return outfile


def process_commands(*cmds):
    for cmd in cmds:
        subprocess.check_output(cmd, shell=True)


def get_forecast_hash(grb):
    """
    Returns a semi-unique identifier for each variable.
    """
    if 'lengthOfTimeRange' in grb.keys():
        fcst_hash = str(grb.name) + '_' \
            + str(grb.lengthOfTimeRange) + 'hr' + '_' \
            + str(grb.stepTypeInternal) + '_'\
            + str(grb.level)
        return fcst_hash

    if grb.name == 'Volumetric soil moisture content':
        fcst_hash = str(grb.name) + '_' \
            + str(grb.stepTypeInternal) + '_'\
            + str(grb.scaledValueOfFirstFixedSurface) + '-'\
            + str(grb.scaledValueOfSecondFixedSurface)
        return fcst_hash

    fcst_hash = str(grb.name) + '_' \
        + str(grb.stepTypeInternal) + '_'\
        + str(grb.level)

    return fcst_hash


def convert_grib2(filename):
    """
    Converts grib file into Wisps Data and writes
    to netCDF.
    """
    # Load lookup table first
    lookup = yamlutil.read_grib2_lookup()
    # Read Control and take out a few variables
    control = yamlutil.read_grib2_control()
    outpath = control['outpath']

    print "reading grib"
    grbs = pygrib.open(filename)
    # Where each index is an hour. So we need the +1 for the last hour
    # Init array and metadata dict
    lead_times = [None] * (max_lead_times + 1)
    metadata = {}
    print "organizing grbs into lead times "
    for grb in grbs:
        # Initialize dictionary at forecast hour if necessary
        if not lead_times[grb.forecastTime]:
            lead_times[grb.forecastTime] = {}

        fcst_hash = get_forecast_hash(grb)
        # Pull out the grb dictionary at the Lead Time
        # NOTE: 'lead_times' is modified since
        #       all_vars is a ptr to a lead_times dict
        all_vars = lead_times[grb.forecastTime]
        if fcst_hash not in all_vars:
            all_vars[fcst_hash] = [grb]
        else:
            all_vars[fcst_hash].append(grb)

    # Temporary grb to grab shared information
    tmp_grb = lead_times[0]
    tmp_grb = tmp_grb[tmp_grb.keys()[0]][0]
    run_time = tmp_grb.hour
    year = tmp_grb.year
    month = tmp_grb.month

    all_objs = []  # Collection of Wisps_data objects
    print "Creating Wisps-data objects for variables at each projection"

    # Loop through each forcast hour.
    # Will typically only do something every third forecast hour, but is not
    # limited.
    data_dict = {}
    for hour in range(max_lead_times):
        all_vars = lead_times[hour]

        # To be on the safe side, instead of striding every 3 hours,
        # It will skip the loop if there're no values in the hour.
        if all_vars is None:
            continue
        # Loop through each variable in every forecast hour
        print "Creating variables for lead time:", hour
        for name, values in all_vars.iteritems():
            # Convert name to the proper wisps name in the db/netcdf.yml
            try:
                name = lookup[name]
            except:
                print 'Warning:', name, 'not in grib2 lookup table'

            stacked = []  # Array to hold stacked grids for every day
            valid = []  # Holds valid time data
            dtype = np.dtype('float32')  # Default
            example_grb = values[0]  # example grib of current variable
            if example_grb.changeDecimalPrecision == 0:
                dtype = np.dtype('int16')
            for grb in values:
                # Add to the validTime array
                valid.append(str(grb.validityDate) +
                             str(grb.validityTime).zfill(4))
                # Add the 2D actual model data
                stacked.append(grb.values)
            stacked = np.dstack(stacked)
            valid = np.array(valid)

            # If it's only one cycle, add a 1 dimensional time component
            if len(stacked.shape) == 2:
                new_shape = list(stacked.shape)
                new_shape.append(1)
                new_shape = tuple(new_shape)
                stacked = np.reshape(stacked, new_shape)

            # Add to the data stacked by lead time
            if name in data_dict:
                data_dict[name]['data'].append(stacked)
                data_dict[name]['lead_time'].append(example_grb.forecastTime)
                data_dict[name]['valid_time'].append(valid)
            else:
                data_dict[name] = {
                    'data': [stacked],
                    'lead_time': [example_grb.forecastTime],
                    'valid_time': [valid]
                }
    ##
    # Format and Write values to NetCDF file
    ##

    # Get standard dimension names
    dimensions = yamlutil.read_dimensions()
    lat = dimensions['lat']
    lon = dimensions['lon']
    lead_time_dim = dimensions['lead_time']
    time = dimensions['time']
    x_proj = dimensions['x_proj']
    y_proj = dimensions['y_proj']
    
    x_proj_data, y_proj_data = get_projection_data(tmp_grb)
    
    fcst_time = run_time
    values = lead_times[0].values()[0]
    for name, grb_dict in data_dict.iteritems():
        stacked = grb_dict['data']
        stacked = np.array(stacked)
        stacked = np.swapaxes(stacked, 0, 2)
        stacked = np.swapaxes(stacked, 0, 1)
        lead_time = grb_dict['lead_time']
        lead_time = np.array([x * Time.ONE_HOUR for x in lead_time])
        valid_time = np.vstack(grb_dict['valid_time'])
        for i, arr in enumerate(valid_time):
            for j, val in enumerate(arr):
                valid_time[i, j] = Time.epoch_time(val)

        valid_time = valid_time.astype(int)
        phenom_time = valid_time
        obj = Wisps_data(name)
        obj.add_source('GFS')
        obj.add_fcstTime(fcst_time)
        obj.dimensions = [y_proj, x_proj, lead_time_dim, time]
        try:
            obj.add_data(stacked)
            obj.change_data_type(dtype)
            all_objs.append(obj)
        except:
            print 'not an numpy array'

        if example_grb.startStep != example_grb.endStep:
            # Then we know it's time bounded
            pass

        # Add PhenomononTime
        #ptime = get_PhenomenonTime(values)
        #obj.time.append(ptime)
        ptime = Time.PhenomenonTime(data=phenom_time)
        obj.time.append(ptime)


        # Add ResultTime
        rtime = get_ResultTime(values)
        obj.time.append(rtime)

        # Add ValidTime
        vstart = valid_time.copy() 
        vend = valid_time.copy()
        for i,j in enumerate(vstart[0]): 
            vstart[:,i] = rtime.data[i]
        valid_time = np.dstack((vstart, vend))
        vtime = Time.ValidTime(data=valid_time)
        #vtime = get_ValidTime(values)
        obj.time.append(vtime)

        # Add ForecastReferenceTime
        ftime = get_ForecastReferenceTime(values)
        obj.time.append(ftime)

        # Add LeadTime
        ltime = get_LeadTime(lead_time)
        obj.time.append(ltime)

    # Make longitude and latitude variables
    lat = Wisps_data('latitude')
    lon = Wisps_data('longitude')
    lat.add_source('GFS')
    lon.add_source('GFS')
    lat.dimensions = ['y', 'x']
    lon.dimensions = ['y', 'x']
    lat_lon_data = tmp_grb.latlons()
    lat.data = lat_lon_data[0]
    lon.data = lat_lon_data[1]
    all_objs.append(lat)
    all_objs.append(lon)

    # Make x and y projection variables
    x_obj = Wisps_data('x_proj')
    x_obj.add_source('GFS')
    x_obj.dimensions = ['x']
    x_obj.data = x_proj_data
    all_objs.append(x_obj)
    y_obj = Wisps_data('y_proj')
    y_obj.add_source('GFS')
    y_obj.dimensions = ['y']
    y_obj.data = y_proj_data
    all_objs.append(y_obj)


    outfile = outpath + get_output_filename(year, month)
    writer.write(all_objs, outfile)

def get_projection_data(grb):
    """Retrieves the projection data from grb
    """
    x_proj = np.zeros(grb.Nx)
    y_proj = np.zeros(grb.Ny)
    prev_val = 0
    for i in range(grb.Nx):
        x_proj[i] = prev_val
        prev_val = prev_val + grb.DxInMetres
    prev_val = 0
    for i in range(grb.Ny):
        y_proj[i] = prev_val 
        prev_val = prev_val + grb.DyInMetres

    return (x_proj,y_proj)


def get_dtype(grb):
    pass


def get_output_filename(year, month):
    """Returns a string representing the netcdf filename of gfs model data"""
    year = str(year)
    month = str(month).zfill(2)
    return 'gfs00' + year + month + '00.nc'


def get_fcst_time(fcst_time_str):
    """
    returns only the forecast hour from the grib string.
    also returns boundedTime information if it exists.
    assumes that bounded Times will have only 2 hours in the string.
    """
    matches = re.findall(r'\d+', fcst_time_str)
    if len(matches) == 0:
        raise IOError("fcst_time_str doesn't contain a numbered hour")
    if len(matches) > 3:
        raise IOError("fcst_time_str contains too many numbers")

    if len(matches) == 1:
        bounded = False
        return (bounded, matches[0])
    if len(matches) == 2:
        bounded = True
        return (bounded, matches[1])


def get_PhenomenonTime(grbs, deep_check=False):
    """Get the Phenomenon Times for a collection of related grbs"""
    if deep_check:
        # Search all grbs to ensure the stride is consistant and dates are in
        # order
        pass
    start_date = str(grbs[0].dataDate)
    start_hour = str(grbs[0].hour).zfill(2)
    end_date = str(grbs[-1].dataDate)
    end_hour = str(grbs[-1].hour).zfill(2)

    ftime = timedelta(hours=grbs[0].forecastTime)

    start = Time.str_to_datetime(start_date + start_hour)
    end = Time.str_to_datetime(end_date + end_hour)
    start = start + ftime
    end = end + ftime
    stride = Time.ONE_DAY
    return Time.PhenomenonTime(start_time=start, end_time=end, stride=stride)


def get_LeadTime(lead_time_data, deep_check=False):
    """Get the Lead Times from a collection of related grbs"""
    return Time.LeadTime(data=lead_time_data)


def get_ValidTime(grbs, deep_check=False):
    """Get the Valid Times for a collection of related grbs"""
    # refer to grb attribute: validityDate and validityTime
    start_date = str(grbs[0].dataDate)
    start_hour = str(grbs[0].hour).zfill(2)
    end_date = str(grbs[-1].dataDate)
    end_hour = str(grbs[-1].hour).zfill(2)

    start = start_date + start_hour
    end = end_date + end_hour
    stride = Time.ONE_DAY
    offset = timedelta(seconds=(grbs[0].forecastTime * Time.ONE_HOUR))
    return Time.ValidTime(start_time=start, end_time=end, stride=stride, offset=offset)


def get_ResultTime(grbs, deep_check=False):
    """Get the Result Times for a collection of related grbs"""
    start_date = str(grbs[0].dataDate)
    start_hour = str(grbs[0].hour).zfill(2)
    end_date = str(grbs[-1].dataDate)
    end_hour = str(grbs[-1].hour).zfill(2)

    start = start_date + start_hour
    end = end_date + end_hour
    stride = Time.ONE_DAY
    return Time.ResultTime(start_time=start, end_time=end, stride=stride)


def get_ForecastReferenceTime(grbs, deep_check=False):
    """Get the ForecastReference Times for a collection of related grbs"""
    if deep_check:
        # Search all grbs to ensure the stride is consistant and dates are in
        # order
        pass
    start_date = str(grbs[0].dataDate)
    start_hour = str(grbs[0].hour).zfill(2)
    end_date = str(grbs[-1].dataDate)
    end_hour = str(grbs[-1].hour).zfill(2)

    start = start_date + start_hour
    end = end_date + end_hour
    stride = Time.ONE_DAY
    return Time.ForecastReferenceTime(start_time=start, end_time=end, stride=stride)


def get_grbs(grbs):
    """
    Parse the grib headers and return an object with relavent attributes.
    grib keys are:

    'parametersVersion'
    'hundred'
    'globalDomain'
    'GRIBEditionNumber'
    'grib2divider'
    'missingValue'
    'ieeeFloats'
    'section0Length'
    'identifier'
    'discipline'
    'editionNumber'
    'totalLength'
    'sectionNumber'
    'section1Length'
    'numberOfSection'
    'centre'
    'centreDescription'
    'subCentre'
    'tablesVersion'
    'masterDir'
    'localTablesVersion'
    'significanceOfReferenceTime'
    'year'
    'month'
    'day'
    'hour'
    'minute'
    'second'
    'dataDate'
    'julianDay'
    'dataTime'
    'productionStatusOfProcessedData'
    'typeOfProcessedData'
    'selectStepTemplateInterval'
    'selectStepTemplateInstant'
    'stepType'
    'sectionNumber'
    'grib2LocalSectionPresent'
    'sectionNumber'
    'gridDescriptionSectionPresent'
    'section3Length'
    'numberOfSection'
    'sourceOfGridDefinition'
    'numberOfDataPoints'
    'numberOfOctectsForNumberOfPoints'
    'interpretationOfNumberOfPoints'
    'PLPresent'
    'gridDefinitionTemplateNumber'
    'shapeOfTheEarth'
    'scaleFactorOfRadiusOfSphericalEarth'
    'scaledValueOfRadiusOfSphericalEarth'
    'scaleFactorOfEarthMajorAxis'
    'scaledValueOfEarthMajorAxis'
    'scaleFactorOfEarthMinorAxis'
    'scaledValueOfEarthMinorAxis'
    'radius'
    'Nx'
    'Ny'
    'latitudeOfFirstGridPoint'
    'latitudeOfFirstGridPointInDegrees'
    'longitudeOfFirstGridPoint'
    'longitudeOfFirstGridPointInDegrees'
    'resolutionAndComponentFlag'
    'LaD'
    'LaDInDegrees'
    'orientationOfTheGrid'
    'orientationOfTheGridInDegrees'
    'Dx'
    'DxInMetres'
    'Dy'
    'DyInMetres'
    'projectionCentreFlag'
    'scanningMode'
    'iScansNegatively'
    'jScansPositively'
    'jPointsAreConsecutive'
    'alternativeRowScanning'
    'iScansPositively'
    'scanningMode5'
    'scanningMode6'
    'scanningMode7'
    'scanningMode8'
    'gridType'
    'sectionNumber'
    'section4Length'
    'numberOfSection'
    'NV'
    'neitherPresent'
    'productDefinitionTemplateNumber'
    'parameterCategory'
    'parameterNumber'
    'parameterUnits'
    'parameterName'
    'typeOfGeneratingProcess'
    'backgroundProcess'
    'generatingProcessIdentifier'
    'hoursAfterDataCutoff'
    'minutesAfterDataCutoff'
    'indicatorOfUnitOfTimeRange'
    'stepUnits'
    'forecastTime'
    'startStep'
    'endStep'
    'stepRange'
    'stepTypeInternal'
    'validityDate'
    'validityTime'
    'typeOfFirstFixedSurface'
    'unitsOfFirstFixedSurface'
    'nameOfFirstFixedSurface'
    'scaleFactorOfFirstFixedSurface'
    'scaledValueOfFirstFixedSurface'
    'typeOfSecondFixedSurface'
    'unitsOfSecondFixedSurface'
    'nameOfSecondFixedSurface'
    'scaleFactorOfSecondFixedSurface'
    'scaledValueOfSecondFixedSurface'
    'pressureUnits'
    'typeOfLevel'
    'level'
    'bottomLevel'
    'topLevel'
    'paramIdECMF'
    'paramId'
    'shortNameECMF'
    'shortName'
    'unitsECMF'
    'units'
    'nameECMF'
    'name'
    'cfNameECMF'
    'cfName'
    'cfVarNameECMF'
    'cfVarName'
    'ifsParam'
    'genVertHeightCoords'
    'PVPresent'
    'sectionNumber'
    'section5Length'
    'numberOfSection'
    'numberOfValues'
    'dataRepresentationTemplateNumber'
    'packingType'
    'referenceValue'
    'referenceValueError'
    'binaryScaleFactor'
    'decimalScaleFactor'
    'bitsPerValue'
    'typeOfOriginalFieldValues'
    'lengthOfHeaders'
    'sectionNumber'
    'section6Length'
    'numberOfSection'
    'bitMapIndicator'
    'bitmapPresent'
    'sectionNumber'
    'section7Length'
    'numberOfSection'
    'codedValues'
    'values'
    'packingError'
    'unpackedError'
    'maximum'
    'minimum'
    'average'
    'numberOfMissing'
    'standardDeviation'
    'skewness'
    'kurtosis'
    'isConstant'
    'changeDecimalPrecision'
    'decimalPrecision'
    'setBitsPerValue'
    'getNumberOfValues'
    'scaleValuesBy'
    'offsetValuesBy'
    'productType'
    'section8Length'
    """
    forecasts = []
    counter = 0
    for grb in grbs:
        print counter
        counter += 1
        grb_info = type('', (), {})()  # Create empty object
        grb_inv = str(grb).split(':')
        grb_info.name = grb_inv[1]
        grb_info.units = grb_inv[2]
        grb_info.projection = grb_inv[3]
        grb_info.coordinate = grb_inv[4]
        grb_info.level = grb_inv[5]
        grb_info.fcst_time = grb_inv[6]
        grb_info.model_run = grb_inv[7]
        grb_info.data = grb.values
        forecasts.append(grb_info)
    return forecasts

# reduce_grib()
# convert_grib2('/scratch3/NCEPDEV/mdl/Riley.Conroy/output/mdl.gfs47.06.pgrb2')
