#! /usr/bin/python
"""This module flags rfi and other forms of bad data.
"""

import os

import numpy.ma as ma
import scipy as sp

import kiyopy.custom_exceptions as ce
import base_single
try :
    import an.rfi as rfi
except ImportError as e:
    print e
    raise RuntimeError("You probably haven't compiled Aravind's RFI flagging "
                       "module written in C.  See the README in "
                       "analysis_IM/an/")

class FlagData(base_single.BaseSingle) :
    """Pipeline module that flags rfi and other forms of bad data.

    For lots of information look at the doc-string for 
    flag_data_aravind.apply_cuts.
    """

    prefix = 'fd_'
    params_init = {
                   # In multiples of the standard deviation of the whole block
                   # once normalized to the time median.
                   'sigma_thres' : 5,
                   # In multiples of the measured standard deviation.
                   'pol_thres' : 5,
                   'pol_width' : 2,
                   'flatten_pol' : True,
                   'derivative_cuts' : 10,
                   'derivative_width' : 2
                   }
    feedback_title = 'New flags each data Block: '
    
    def action(self, Data) :
        already_flagged = ma.count_masked(Data.data)
        params = self.params
        apply_cuts(Data, sig_thres=params['sigma_thres'], 
                   pol_thres=params['pol_thres'],
                   width=params['pol_width'], flatten=params['flatten_pol'],
                   der_flags=params['derivative_cuts'], 
                   der_width=params['derivative_width'])
        new_flags = ma.count_masked(Data.data) - already_flagged
        self.block_feedback = str(new_flags) + ', '

        Data.add_history('Flagged Bad Data.', ('Sigma threshold: ' +
                    str(self.params['sigma_thres']), 'Polarization threshold: '
                    + str(self.params['pol_thres'])))
        return Data

def apply_cuts(Data, sig_thres=5.0, pol_thres=5.0, width=2, flatten=True,
               der_flags=10, der_width=2) :
    """Flags bad data from RFI and far outliers..
    
    Masks any data that is further than 'sig_thres' sigmas from the mean of the
    entire data block (all frequencies, all times).  Each cal, fequency and
    polarization is first normalized to the time mean.  Cut only checked for XX
    and YY polarizations but all polarizations are masked.  This cut is
    deactivated by setting 'sig_thres' < 0.

    Aravind rfi cut is also performed, cutting data with polarization lines, as
    well as lines in XX and YY.

    Arguments:
        Data -      A DataBlock object containing data to be cleaned.
        sig_thres - (float) Any XX or YY data that deviates by this more than 
                    this many sigmas is flagged.  Data first normalized to 
                    time median.
        pol_thres - (float) Any data cross polarized by more than this 
                    many sigmas is flagged.
        width -     (int) In the polaridezation cut, flag data within this many
                    frequency bins of offending data.
        flatten -   (Bool) Preflatten the polarization spectrum.
        der_flags - (int) Find RFI in XX and YY by looking for spikes in the
                    derivative.  Flag this many spikes.
        der_width - (int) Same as width but for the derivative cut.
    """
    
    # Here we check the polarizations and cal indicies
    if tuple(Data.field['CRVAL4']) == (-5, -7, -8, -6) :
        xx_ind = 0
        yy_ind = 3
        pol_inds = [1,2]
        norm_inds = [0, 3]
    elif tuple(Data.field['CRVAL4']) == (1, 2, 3, 4) :
        pol_inds = [2,3]
        xx_ind = 0
        yy_ind = 1
        norm_inds = [0,0]
    else :
        raise ce.DataError('Polarization types not as expected,'
                           ' function needs to be generalized.')
    if pol_thres > 0 :
        Data.calc_freq()
        freq = Data.freq
        dims = Data.dims
        data = Data.data
        derivative_cut = 0
        if der_flags > 0:
            derivative_cut = 1

        # Allocate memory in SWIG array types.
        #fit = sp.empty((dims[3],), dtype=sp.float64)
        fit = sp.arange(dims[3], dtype=sp.float64)
        mask = sp.empty((dims[3],), dtype=sp.int32)

        # Outer loops performed in python.
        for tii in range(dims[0]) :
            for cjj in range(dims[2]) :
                # Polarization cross correlation coefficient.
                cross = ma.sum(data[tii,pol_inds,cjj,:]**2, 0)
                cross /= (data[tii,norm_inds[0],cjj,:] *
                          data[tii,norm_inds[1],cjj,:])
                cross = ma.filled(cross, 1000.)
                # Copy data to the SWIG arrays.
                # This may be confusing: Data is a DataBlock object which has
                # an attribute data which is a masked array.  Masked arrays
                # have attributes data (an array) and mask (a bool array).  So
                # thisdata and thismask are just normal arrays.
                status = rfi.get_fit(cross,freq,fit)
                if status :
                    raise ce.DataError("Problem with data flagger.")
                # XX polarization.
                data_array = sp.asarray(data[tii,xx_ind,cjj,:])
                status =  rfi.clean(pol_thres, width, int(flatten), 
                                    derivative_cut, der_flags, der_width, fit,
                                    cross, data_array, freq, mask)
                if status :
                    raise ce.DataError("Problem with data flagger.")
                # Copy mask the flagged points and set up for YY.
                bad_inds, = sp.where(mask)
                data[tii,:,cjj,bad_inds] = ma.masked
                data_array = sp.asarray(data[tii,yy_ind,cjj,:])
                # Clean The YY pol.
                status = rfi.clean(pol_thres, width, int(flatten),
                                   derivative_cut, der_flags, der_width, fit,
                                   cross, data_array, freq, mask)
                if status :
                    raise ce.DataError("Problem with data flagger.")
                # Mask flagged YY data.
                bad_inds, = sp.where(mask)
                data[tii,:,cjj,bad_inds] = ma.masked
    
    if sig_thres > 0 :
        # Will work with squared data so no square roots needed.
        nt = Data.dims[0]
        data = Data.data[:, :, :, :]
        norm_data = (data/ma.mean(data, 0) - 1)**2
        var = ma.mean(norm_data, 0)
        # Use an iteratively flagged mean instead of a median as medians are
        # very computationally costly.
        for jj in range(3) :
            # First check for any outliers that could throw the initial mean off
            # more than sqrt(3*nt/4) sigma.  This is the 'weak' cut.
            bad_mask = ma.where(sp.any(norm_data[:,:,:,:] >
                                       3.*nt*var[:,:,:]/4., 1)) 
            if len(bad_mask[0]) == 0:
                break
            for ii in range(4) :
                data_this_pol = Data.data[:,ii,:,:]
                data_this_pol[bad_mask] = ma.masked
            # Recalculate the mean and varience for the next iteration or step.
            norm_data = (data/ma.mean(data, 0) - 1)**2
            var = ma.mean(norm_data, 0)

        # Strong cut, flag data deviating from the t and f var.
        # Want the varience over t,f. mean_f(var_t(data)) = var_t,f(data)
        var = ma.mean(var, -1)
        bad_mask = ma.where(sp.any(norm_data[:,:,:,:] > 
                                   var[:,:,sp.newaxis]*sig_thres**2, 1))
        for ii in range(4) :
            data_this_pol = Data.data[:,ii,:,:]
            data_this_pol[bad_mask] = ma.masked



# If this file is run from the command line, execute the main function.
if __name__ == "__main__":
    import sys
    FlagData(str(sys.argv[1])).execute()

