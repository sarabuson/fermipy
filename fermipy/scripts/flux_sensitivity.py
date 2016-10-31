# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import absolute_import, division, print_function

import os
import argparse

import pyLikelihood as pyLike

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table, Column
from astropy.io import fits

from fermipy import utils
from fermipy import spectrum
from fermipy import irfs
from fermipy import skymap


class SensitivityCalc(object):

    def __init__(self, gdiff, iso, ltc, ebins, event_class, event_types):
        """ 

        Parameters
        ----------
        gdiff : `~fermipy.skymap.SkyMap`
            Galactic diffuse map cube function.

        iso : `~numpy.ndarray`
            Array of isotropic intensity vs. energy.

        ltc : `~fermipy.irfs.LTCube`

        """
        self._gdiff = gdiff
        self._iso = iso
        self._ltc = ltc
        self._ebins = ebins
        self._log_ebins = np.log10(ebins)
        self._ectr = np.exp(utils.edge_to_center(np.log(self._ebins)))
        self._event_class = event_class
        self._event_types = event_types

        self._psf = []
        self._exp = []

        ebins = 10**np.linspace(1.0,6.0,5*8+1)
        skydir = SkyCoord(0.0,0.0,unit='deg')        
        for et in self._event_types:
            self._psf += [irfs.PSFModel(skydir.icrs, self._ltc,
                                        self._event_class, et,
                                        np.log10(ebins))]
            self._exp += [irfs.ExposureMap.create(self._ltc,
                                                  self._event_class, et,
                                                  ebins)]

    @property
    def ebins(self):
        return self._ebins

    @property
    def ectr(self):
        return self._ectr
    
    def compute_counts(self, skydir, fn, ebins=None):
        """Compute signal and background counts.

        Parameters
        ----------
        ebins : `~numpy.ndarray`

        Returns
        -------
        sig : `~numpy.ndarray`
            Signal counts array.  Dimensions are energy, angular
            separation, and event type.

        bkg : `~numpy.ndarray`

        """

        if ebins is None:
            ebins = self.ebins
            ectr = self.ectr
        else:
            ectr = np.exp(utils.edge_to_center(np.log(ebins)))
            
        sig = []
        bkg = []
        for psf, exp in zip(self._psf,self._exp):
            expv = exp.interpolate(skydir.icrs.ra.deg, skydir.icrs.dec.deg, ectr)            
            bkgv = self._gdiff.interpolate(skydir.l.deg, skydir.b.deg, ectr)
            isov = np.exp(np.interp(np.log(ectr), np.log(self._iso[0]),
                                    np.log(self._iso[1])))
            bkgv += isov
            s, b = irfs.compute_ps_counts(ebins, expv, psf, bkgv, fn)
            sig += [s]
            bkg += [b]

        sig = np.concatenate([np.expand_dims(t, -1) for t in sig])
        bkg = np.concatenate([np.expand_dims(t, -1) for t in bkg])
        return sig, bkg

    def diff_flux_threshold(self, skydir, fn, ts_thresh, min_counts):

        sig, bkg = self.compute_counts(skydir, fn)
    
        norms = irfs.compute_norm(sig, bkg, ts_thresh,
                                  min_counts, sum_axes=[1, 2])
        npred = np.squeeze(np.apply_over_axes(np.sum, norms * sig, [1, 2]))
        norms = np.squeeze(norms)
        flux = norms * fn.flux(self.ebins[:-1], self.ebins[1:])
        eflux = norms * fn.eflux(self.ebins[:-1], self.ebins[1:])
        dnde = norms * fn.dnde(self.ectr)
        e2dnde = self.ectr**2 * dnde
        
        return dict(e_min=self.ebins[:-1],e_max=self.ebins[1:],
                    e_ref=self.ectr,
                    npred=npred, flux=flux, eflux=eflux,
                    dnde=dnde, e2dnde=e2dnde)

    def int_flux_threshold(self, skydir, fn, ts_thresh, min_counts):

        ebins = 10**np.linspace(np.log10(self.ebins[0]),
                                np.log10(self.ebins[-1]),32)
        ectr = np.sqrt(ebins[0]*ebins[-1])
        
        sig, bkg = self.compute_counts(skydir, fn, ebins)
        
        norms = irfs.compute_norm(sig, bkg, ts_thresh,
                                  min_counts, sum_axes=[0, 1, 2])
        npred = np.squeeze(np.apply_over_axes(np.sum, norms * sig, [0, 1, 2]))
        norms = np.squeeze(norms)
        flux = norms * fn.flux(ebins[0], ebins[1])
        eflux = norms * fn.eflux(ebins[0], ebins[1])
        dnde = norms * fn.dnde(ectr)
        e2dnde = ectr**2 * dnde

        o = dict(e_min=self.ebins[0],e_max=self.ebins[-1],e_ref=fn.scale,
                 npred=npred, flux=flux, eflux=eflux,
                 dnde=dnde, e2dnde=e2dnde)
        
        sig, bkg = self.compute_counts(skydir, fn)
        npred = np.squeeze(np.apply_over_axes(np.sum, norms * sig, [1, 2]))
        flux = norms * fn.flux(self.ebins[:-1], self.ebins[1:])
        eflux = norms * fn.eflux(self.ebins[:-1], self.ebins[1:])
        dnde = norms * fn.dnde(self.ectr)
        e2dnde = ectr**2 * dnde
        
        o['bins'] = dict(npred = npred,
                         flux = flux,
                         eflux = eflux,
                         dnde = dnde,
                         e2dnde = e2dnde,
                         e_min=self.ebins[:-1], e_max=self.ebins[1:], e_ref=self.ectr)
        
        return o
        
    
def main():
    usage = "usage: %(prog)s [options]"
    description = "Calculate the LAT point-source flux sensitivity."
    parser = argparse.ArgumentParser(usage=usage, description=description)

    parser.add_argument('--ltcube', default=None,
                        help='Set the path to the livetime cube.')
    parser.add_argument('--galdiff', default=None, required=True,
                        help='Set the path to the galactic diffuse model.')
    parser.add_argument('--isodiff', default=None,
                        help='Set the path to the isotropic model.  If none then the '
                        'default model will be used for the given event class.')
    parser.add_argument('--ts_thresh', default=25.0, type=float,
                        help='Set the detection threshold.')
    parser.add_argument('--min_counts', default=3.0, type=float,
                        help='Set the minimum number of counts.')
    parser.add_argument('--joint', default=False, action='store_true',
                        help='Compute sensitivity using joint-likelihood of all event types.')
    parser.add_argument('--event_class', default='P8R2_SOURCE_V6',
                        help='Set the IRF name (e.g. P8R2_SOURCE_V6).')
    parser.add_argument('--glon', default=0.0, type=float,
                        help='Galactic longitude.')
    parser.add_argument('--glat', default=0.0, type=float,
                        help='Galactic latitude.')
    parser.add_argument('--index', default=2.0, type=float,
                        help='Source power-law index.')
    parser.add_argument('--emin', default=10**1.5, type=float,
                        help='Minimum energy in MeV.')
    parser.add_argument('--emax', default=10**6.0, type=float,
                        help='Maximum energy in MeV.')
    parser.add_argument('--nbin', default=18, type=int,
                        help='Number of energy bins for differential flux calculation.')
    parser.add_argument('--output', default='output.fits', type=str,
                        help='Output filename.')
    parser.add_argument('--obs_time_yr', default=None, type=float,
                        help='Rescale the livetime cube to this observation time in years.  If none then the '
                        'calculation will use the intrinsic observation time of the livetime cube.')

    args = parser.parse_args()
    event_types = [['FRONT', 'BACK']]
    fn = spectrum.PowerLaw([1E-13, -args.index], scale=1E3)

    log_ebins = np.linspace(np.log10(args.emin),
                            np.log10(args.emax), args.nbin + 1)
    ebins = 10**log_ebins
    ectr = np.exp(utils.edge_to_center(np.log(ebins)))

    c = SkyCoord(args.glon, args.glat, unit='deg', frame='galactic')

    if args.ltcube is None:

        if args.obs_time_yr is None:
            raise Exception('No observation time defined.')

        ltc = irfs.LTCube.create_empty(0, args.obs_time_yr * 365 * 24 * 3600.,
                                       args.obs_time_yr * 365 * 24 * 3600.)
        ltc._counts *= ltc.domega[:, np.newaxis] / (4. * np.pi)
    else:
        ltc = irfs.LTCube.create(args.ltcube)
        if args.obs_time_yr is not None:
            ltc._counts *= args.obs_time_yr * 365 * \
                24 * 3600. / (ltc.tstop - ltc.tstart)

    gdiff = skymap.Map.create_from_fits(args.galdiff)

    if args.isodiff is None:
        isodiff = utils.resolve_file_path('iso_%s_v06.txt' % args.event_class,
                                          search_dirs=[os.path.join('$FERMIPY_ROOT', 'data'),
                                                       '$FERMI_DIFFUSE_DIR'])
        isodiff = os.path.expandvars(isodiff)
    else:
        isodiff = args.isodiff

    iso = np.loadtxt(isodiff, unpack=True)
    
    scalc = SensitivityCalc(gdiff,iso,ltc, ebins,
                            args.event_class, event_types)

    o = scalc.diff_flux_threshold(c,fn,args.ts_thresh,
                                  args.min_counts)
    
    cols = [Column(name='e_min', dtype='f8', data=scalc.ebins[:-1], unit='MeV'),
            Column(name='e_ref', dtype='f8', data=o['e_ref'], unit='MeV'),
            Column(name='e_max', dtype='f8', data=scalc.ebins[1:], unit='MeV'),
            Column(name='flux', dtype='f8', data=o['flux'], unit='ph / (cm2 s)'),
            Column(name='eflux', dtype='f8', data=o['eflux'], unit='MeV / (cm2 s)'),
            Column(name='dnde', dtype='f8', data=o['dnde'],
                   unit='ph / (MeV cm2 s)'),
            Column(name='e2dnde', dtype='f8',
                   data=o['e2dnde'], unit='MeV / (cm2 s)'),
            Column(name='npred', dtype='f8', data=o['npred'], unit='ph')]

    tab_diff = Table(cols)

    cols = [Column(name='index', dtype='f8'),
            Column(name='e_min', dtype='f8', unit='MeV'),
            Column(name='e_ref', dtype='f8', unit='MeV'),
            Column(name='e_max', dtype='f8', unit='MeV'),
            Column(name='flux', dtype='f8', unit='ph / (cm2 s)'),
            Column(name='eflux', dtype='f8', unit='MeV / (cm2 s)'),
            Column(name='dnde', dtype='f8', unit='ph / (MeV cm2 s)'),
            Column(name='e2dnde', dtype='f8', unit='MeV / (cm2 s)'),
            Column(name='npred', dtype='f8', unit='ph')]

    cols_ebin = [Column(name='index', dtype='f8'),
                 Column(name='e_min', dtype='f8', unit='MeV',shape=(len(ectr),)),
                 Column(name='e_ref', dtype='f8', unit='MeV',shape=(len(ectr),)),
                 Column(name='e_max', dtype='f8', unit='MeV',shape=(len(ectr),)),
                 Column(name='flux', dtype='f8', unit='ph / (cm2 s)',shape=(len(ectr),)),
                 Column(name='eflux', dtype='f8', unit='MeV / (cm2 s)',shape=(len(ectr),)),
                 Column(name='dnde', dtype='f8', unit='ph / (MeV cm2 s)',shape=(len(ectr),)),
                 Column(name='e2dnde', dtype='f8', unit='MeV / (cm2 s)',shape=(len(ectr),)),
                 Column(name='npred', dtype='f8', unit='ph',shape=(len(ectr),))]

    tab_int = Table(cols)
    tab_int_ebin = Table(cols_ebin)
    
    index = np.linspace(1.0,5.0,4*4+1)
    
    for g in index:
        fn = spectrum.PowerLaw([1E-13, -g], scale=10**3.5)
        o = scalc.int_flux_threshold(c,fn,args.ts_thresh, 3.0)
        row = [g]
        for colname in tab_int.columns:
            if not colname in o:
                continue            
            row += [o[colname]]

        tab_int.add_row(row)
            
        row = [g]
        for colname in tab_int.columns:
            if not colname in o:
                continue            
            row += [o['bins'][colname]]            
        tab_int_ebin.add_row(row)

    hdulist = fits.HDUList()
    hdulist.append(fits.table_to_hdu(tab_diff))
    hdulist.append(fits.table_to_hdu(tab_int))
    hdulist.append(fits.table_to_hdu(tab_int_ebin))

    hdulist[1].name = 'DIFF_FLUX'
    hdulist[2].name = 'INT_FLUX'
    hdulist[3].name = 'INT_FLUX_EBIN'
    
    hdulist.writeto(args.output, clobber=True)

if __name__ == "__main__":
    main()
