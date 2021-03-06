#!/usr/bin/env python
import sys
import os
file_dir = os.path.dirname(os.path.realpath(__file__))
relative_path = "/.."
path = os.path.abspath(file_dir + relative_path)
sys.path.insert(0, path)

import pytdlpack as ptdl
import yaml
import time
import numpy as np
import argparse
import pdb
import registry.util as cfg
import metar_to_nc.util as util
import core.writer as writer
import core.Time as Time
from netCDF4 import Dataset
from collections import OrderedDict
from core.Camps_data import Camps_data
from datetime import datetime
import scripts.metar_driver as metar_driver



"""Module: convert_tdlpack.py
This module is incomplete.  Do not use!!!

Class: point

    Methods:
        __init__
        add_predictor

Methods:
    get_point_list
    parse_grid_definition
    parse_product_definition
    write_model_to_netcdf
    write_obs_to_netcdf
    write_station_names
    is_obs_tdl_file
    convert_model
    create_camps_objs
    get_name
    convert_obs
    find_number_of_records_in_one_timestep
    find_all_obs_types
    find_number_of_timesteps
    parse_hour_to_datetime
"""



MISSING_VALUE = 9999


class point:
    def __init__(self, name, obs_types, num_timesteps):
        self.name = name
        self.dates = np.zeros(num_timesteps)
        self.predictors = {}
        for o in obs_types:
            self.predictors[o] = np.full((num_timesteps), MISSING_VALUE)

    def add_predictor(self, predictor_id):
        self.predictors[predictor_id] = np.full(
            (len(self.dates)), MISSING_VALUE)


#NOTE: incomplete
#def get_point_list(points):
#    """ Returns a 4 by n charactar array representing the name of the point """
#
#    for p in points:
#        np.array


def parse_grid_definition(grid_def):
    """Parses a grid definition section array that is in
    a ptdl record. Returns dictionary.
    """

    grid_definition = {}
    grid_definition['section_length'] = grid_def[0]
    grid_definition['map_projection'] = grid_def[1]
    grid_definition['NX'] = grid_def[2]
    grid_definition['NY'] = grid_def[3]
    grid_definition['lower_left_lat'] = grid_def[4]  # degrees * 10000
    grid_definition['lower_left_lon'] = grid_def[5]  # degrees * 10000
    grid_definition['grid_orientation'] = grid_def[6]  # degrees * 10000
    grid_definition['grid_length'] = grid_def[7]  # in mm
    grid_definition['lat_at_grid_length'] = grid_def[8]

    return grid_definition


def parse_product_definition(prod_def):
    """Parses a product definition section array that is in
    a ptdl record. Returns dictionary.
    """

    product_definition = {}
    product_definition['section_length'] = prod_def[0]
    product_definition['grid_defined'] = prod_def[1]
    product_definition['year'] = prod_def[2]
    product_definition['month'] = prod_def[3]
    product_definition['day'] = prod_def[4]
    product_definition['hour'] = prod_def[5]
    product_definition['minutes'] = prod_def[6]
    product_definition['formatted_date'] = prod_def[7]
    product_definition['id_1'] = prod_def[8]
    product_definition['id_2'] = prod_def[9]
    product_definition['id_3'] = prod_def[10]
    product_definition['id_4'] = prod_def[11]
    product_definition['projection_hours'] = prod_def[12]  # hours
    product_definition['projection_minutes'] = prod_def[13]
    product_definition['model'] = prod_def[14]
    product_definition['sequence_number'] = prod_def[15]
    product_definition['decimal_scale_factor'] = prod_def[16]
    product_definition['binary_scale_factor'] = prod_def[17]

    return product_definition


#NOTE: incomplete function
def write_model_to_netcdf(filepath, var_dict):

    nc = Dataset(filename, mode='w', format="NETCDF4")
    for i in var:
        pdb.set_trace()


def get_obs_type(all_names):
    """Returns the observation type based on all_names."""

    if len(all_names) > 10000:  # It's mesonet
        return 'MESONET'

    if len(all_names) > 1300:  # It's metar
        return 'METAR'

    return "MARINE"


def write_obs_to_netcdf(filepath, point_dict):
    """Writes a Point dictionary of objects to a netcdf file.
    Expects point_dict to have the same shape and number of
    predictors.
    """

    cfg.read_nc_config()
    mosID_lookup = cfg.read_mosID_lookup()
    temp_obs = []
    #nc,var_dict = util.init_netcdf_output(filepath)
    all_predictors = point_dict.values()[0].predictors.keys()  # mosIDs
    sorted_point_dict = OrderedDict(sorted(point_dict.items()))
    all_points = sorted_point_dict.values()
    all_names = sorted_point_dict.keys()

    start_date = 0
    end_date = 0
    i = 0
    while start_date == 0 or end_date == 0:
        temp_obs = sorted_point_dict.values()[1].dates
        start_date = str(int(temp_obs[0]))
        end_date = str(int(temp_obs[-1]))
        i += 1
    logging.info("Start date: "+start_date)
    logging.info("End date: "+end_date)

    # TODO: find better way to do this
    ob_type = get_obs_type(all_names)
    all_obj = []
    for p in all_predictors:
        try:
            nc_var_name = mosID_lookup[p]
        except KeyError:
            logging.info(str(p)+" is not in MOS ID to Netcdf lookup table. Skipping...")
            continue
        logging.info("Creating: "+nc_var_name)
        temp_obs = []
        for v in all_points:
            temp_obs.append(v.predictors[p])
        temp_obs = np.vstack(temp_obs)
        if nc_var_name == 'observation_time':
            continue

        obj = Camps_data(nc_var_name)
        obj.set_dimensions()
        if nc_var_name == 'observation_time':
            temp_obs = temp_obs[0, :]
        obj.add_data(temp_obs)
        obj.add_source(ob_type)
        if(ob_type == "MESONET"):
            obj.add_process('MesoObProcStep1')
            obj.add_process('MesoObProcStep2')
            obj.add_process('MesoObProcStep3')
        obj.time = metar_driver.add_time(start_date, end_date)
        obj.change_data_type()
        #obj.time = metar_driver.add_time(start_date, end_date)
        all_obj.append(obj)

    obj = metar_driver.pack_station_names(all_names)
    obj.add_source(ob_type)
    all_obj.append(obj)

    logging.info("Writing to output file "+filepath)
    writer.write(all_obj, filepath)


def write_station_names(var_dict, names):
    """Write station names."""

    station_name_arr = []
    for name in names:
        char_arr = np.array(list(name), 'c')
        if len(station_name_arr) == 0:
            station_name_arr = char_arr
        else:
            station_name_arr = np.vstack((station_name_arr, char_arr))

    var_dict['station'][:] = station_name_arr


def is_obs_tdl_file(tdl_filepath):
    """Returns True if is detects that the file is an obs file."""

    return 'gfs' not in tdl_filepath #Weak criterion.


#NOTE: This function is incomplete.
def convert_model(filepath, out_dir="./"):
    """Converts data in TDLpack at location filepath to netcdf file
    at hard-coded filepath '/scratch3/NCEPDEV/mdl/Riley.Conroy/output/test.nc'."""

    fcst_time = filepath[-3:-1] #this is model cycle time in hour-of-day
    mosPL_lookup = cfg.read_gfs_tdlpack_lookup()
    num_lats = 169 #NOTE: why is this hard-coded?
    num_lons = 297 #NOTE: why is this hard-coded?

    print("Opening TDLPack file")
    records = ptdl.TdlpackDecode(filepath)
    print("Finished Reading")

    #NOTE: 'var_dict' and 'alt_var_dict' initialized BUT not filled downstream!!?
    var_dict = {}  # forecast time <= 192
    alt_var_dict = {}  # forecast time > 192
    records_length = len(records)
    start_date = records[0].date
    end_date = records[-1].date
    for i, r in enumerate(records):
        data = r.unpackData()
        name = get_name(r)
        if r.product_definition_section[10] <= 192:
            cur_dict = var_dict
        else:
            cur_dict = alt_var_dict
        if name not in cur_dict:
            #cur_dict[name] = data
            cur_dict[name] = [data]
        else:
            try:
                #cur_dict[name] = np.dstack((cur_dict[name],data))
                cur_dict[name].append(data)
            except Exception as e:
                pdb.set_trace()

    camps_objs = []
    for i in var_dict.keys():
        num_days = len(var_dict[i])
        var_dict[i] = np.dstack(var_dict[i])
        var_dict[i] = np.reshape(var_dict[i], (num_lats, num_lons, num_days))
        camps_name = mosPL_lookup[i[0]]
        lead_hours = name[1]
        obj = Camps_data(camps_name)
        obj.set_dimensions(('latitude', 'longitude', 'time'))
        obj.add_source("GFS")
        obj.add_leadTime(lead_hours)
        obj.add_fcstTime(fcst_time)
        obj.data = var_dict[i]
        camps_objs.append(obj)

    # write destination is hard-coded!!!
    writer.write(
        camps_objs, '/scratch3/NCEPDEV/mdl/Riley.Conroy/output/test.nc')

    return var_dict


#NOTE: does nothing
def create_camps_objs(var_dict):

    for var in var_dict.keys():
        pass


def get_name(record):

    name = record.plain_language.strip()
    projection = record.product_definition_section[12]

    # return name+"_"+str(projection).zfill(3)
    return (name, projection)


def convert_obs(filepath, out_dir="./"):
    """Given a path to a tdlpack file that has observations,
    converts file into a camps formatted netcdf file.

    Note: In tdlpack there is a separate record for every
    different variable and timestep. For observations each
    record will contain a 1D array in the 'values' member,
    which are of dimension station. The record also has the
    station list in the 'ccall' member.
    """

    # Return if tdlpack file does not exist at 'filepath'.
    if not os.path.isfile(filepath):
        print("file: " + filepath + " Not found")
        return

    point_dict = {}
    records = ptdl.TdlpackDecode(filepath)
    obs_types = find_all_obs_types(records)
    num_timesteps = find_number_of_timesteps(records)
    print("number of timesteps ",str(num_timesteps))

    time_index = 0
    cur_date = records[0].date
    init_time = time.time()
    for rnum, r in enumerate(records):
        if r.date != cur_date:
            ########################
            # if int(r.date) % 10 == 0:
            #    ## Time test ##
            #    diff_time = time.time() - init_time
            #    init_time = time.time()
            #    print('elapsed time:',diff_time
            ########################
            time_index += 1
            cur_date = r.date
        record = r.values
        str_id = str(r.id[0])
        # r.ccall is a list of all stations for current record
        for i, call in enumerate(r.ccall):
            if call not in point_dict:
                new_point = point(call, obs_types, num_timesteps)
                new_point.dates[time_index] = cur_date
                point_dict[call] = new_point

            cur_point = point_dict[call]  # O(1)
            # not necessarily needed, could make shorter
            cur_point.dates[time_index] = cur_date
            cur_point.predictors[str_id][time_index] = record[i]  # O(1)

    write_obs_to_netcdf(
        out_dir + os.path.basename(filepath) + ".nc", point_dict)


def find_number_of_records_in_one_timestep(records):
    """Returns number of records within the first day of the series 'records'."""

    start_date = records[0].date
    for i, r in enumerate(records):
        if r.date != start_date:
            return i


def find_all_obs_types(records):
    """Returns set of observation types in series 'records'. """

    obs_types = set()
    for r in records:
        obs_types.add(str(r.id[0]))

    return obs_types


def find_number_of_timesteps(records):
    """Returns number of one-hourly steps in time range
    spanned by the series 'records'.
    """

    first_record = records[0]
    last_record = records[-1]
    first_record_date = parse_hour_to_datetime(first_record.date)
    last_record_date = parse_hour_to_datetime(last_record.date)
    delta_time = last_record_date - first_record_date
    steps = delta_time.days * 24
    steps += delta_time.seconds / 60 / 60

    return steps + 1


def parse_hour_to_datetime(str_date):
    """Assumed that time is in form YYYYMMDDHH, which is the format of the
    hours array.
    """

    str_date = str(str_date)
    year = int(str_date[:4])
    month = int(str_date[4:6])
    day = int(str_date[6:8])
    hour = int(str_date[8:10])

    return datetime(year, month, day, hour)
