# ########################################################################### #
#    Copyright (c) 2019-2020, California Institute of Technology.
#    All rights reserved.  Based on Government Sponsored Research under
#    contracts NNN12AA01C, NAS7-1407 and/or NAS7-03001.
#
#    Redistribution and use in source and binary forms, with or without
#    modification, are permitted provided that the following conditions
#    are met:
#      1. Redistributions of source code must retain the above copyright
#         notice, this list of conditions and the following disclaimer.
#      2. Redistributions in binary form must reproduce the above copyright
#         notice, this list of conditions and the following disclaimer in
#         the documentation and/or other materials provided with the
#         distribution.
#      3. Neither the name of the California Institute of
#         Technology (Caltech), its operating division the Jet Propulsion
#         Laboratory (JPL), the National Aeronautics and Space
#         Administration (NASA), nor the names of its contributors may be
#         used to endorse or promote products derived from this software
#         without specific prior written permission.
#
#    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#    "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#    LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
#    A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE CALIFORNIA
#    INSTITUTE OF TECHNOLOGY BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
#    SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
#    TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
#    PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
#    LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
#    NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
#    SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# ########################################################################### #
#    EXOplanet Transit Interpretation Code (EXOTIC)
#    # NOTE: See companion file version.py for version info.
# ########################################################################### #
# ########################################################################### #
# Various functions for fitting a linear model to data, including nested 
# sampling, linear least squares. Includes residual plotting, posteriors and 
# a periodogram analysis.
# 
# ########################################################################### #

import copy
import numpy as np
from itertools import cycle
import statsmodels.api as sm
import pandas.util.testing as tm
import matplotlib.pyplot as plt
from exotic.api.plotting import corner
from ultranest import ReactiveNestedSampler
from astropy.timeseries import LombScargle


class linear_fitter(object):

    def __init__(self, data, dataerr, bounds=None, prior=None):
        """
        Fit a linear model to data using nested sampling.

        Parameters
        ----------
        data : array
            Data to fit.
        dataerr : array
            Error on data.
        bounds : dict, optional
            Bounds on parameters. Dictionary of tuples/list
        prior : dict, optional
            Prior on parameters for slope and intercept. Dictionary of tuples/list
        """
        self.data = data
        self.dataerr = dataerr
        self.bounds = bounds
        self.prior = prior.copy() # dict {'m':(0.1,0.5), 'b':(0,1)}
        if bounds is None:
            # use +- 3 sigma prior as bounds
            self.bounds = {
                'm':[prior['m'][0]-3*prior['m'][1],prior['m'][0]+3*prior['m'][1]],
                'b':[prior['b'][0]-3*prior['b'][1],prior['b'][0]+3*prior['b'][1]]
            }
        self.fit_nested()

    def fit_nested(self):
        """ Fit a linear model to data using nested sampling. """

        freekeys = list(self.bounds.keys())
        boundarray = np.array([self.bounds[k] for k in freekeys])
        bounddiff = np.diff(boundarray,1).reshape(-1)
        self.epochs = np.round((self.data - np.mean(self.bounds['b']))/np.mean(self.bounds['m']))

        def loglike(pars):
            # chi-squared
            model = pars[0]*self.epochs + pars[1]
            return -0.5 * np.sum( ((self.data-model)/self.dataerr)**2 )
        
        def prior_transform(upars):
            # transform unit cube to prior volume
            return (boundarray[:,0] + bounddiff*upars)

        # estimate slope and intercept
        self.results = ReactiveNestedSampler(freekeys, loglike, prior_transform).run(max_ncalls=4e5,min_num_live_points=420, show_status=False)

        # alloc data for best fit + error
        self.errors = {}
        self.quantiles = {}
        self.parameters = {}

        for i, key in enumerate(freekeys):
            self.parameters[key] = self.results['maximum_likelihood']['point'][i]
            self.errors[key] = self.results['posterior']['stdev'][i]
            self.quantiles[key] = [
                self.results['posterior']['errlo'][i],
                self.results['posterior']['errup'][i]]

        # final model
        self.model = self.epochs * self.parameters['m'] + self.parameters['b']
        self.residuals = self.data - self.model

    def plot_oc(self, savefile=None, ylim='none', show_2sigma=False):
        """ Plot the data in the form of residuals vs. time

        Parameters
        ----------
        savefile : str, optional
            Save the figure to a file.
        ylim : str, optional
            Set the y-axis limits. Default is 'none'. Can be prior, average, or none.
        show_2sigma : bool, optional
            Show a fill between using the 2 sigma limits. Default is False (aka 1 sigma)
        """

        # set up the figure        
        fig,ax = plt.subplots(1, figsize=(9,6))

        # plot the data/residuals
        ax.errorbar(self.epochs, self.residuals*24*60, yerr=self.dataerr*24*60, ls='none', marker='o',color='black')
        ylower = (self.residuals.mean()-3*np.std(self.residuals))*24*60
        yupper = (self.residuals.mean()+3*np.std(self.residuals))*24*60

        # upsample data
        epochs = (np.linspace(self.data.min()-7, self.data.max()+7, 1000) - self.parameters['b'])/self.parameters['m']

        # set the y-axis limits
        depoch = self.epochs.max() - self.epochs.min()
        ax.set_xlim([self.epochs.min()-depoch*0.01, self.epochs.max()+depoch*0.01])

        # best fit solution
        model = epochs*self.parameters['m'] + self.parameters['b']
    
        # MonteCarlo the new ephemeris for uncertainty
        mc_m = np.random.normal(self.parameters['m'], self.errors['m'], size=10000)
        mc_b = np.random.normal(self.parameters['b'], self.errors['b'], size=10000)
        mc_model = np.expand_dims(epochs,-1) * mc_m + mc_b

        # create a fill between area for uncertainty of new ephemeris
        diff = mc_model.T - model

        if show_2sigma:
            ax.fill_between(epochs, np.percentile(diff,2,axis=0)*24*60, np.percentile(diff,98,axis=0)*24*60, alpha=0.2, color='k', label=r'Uncertainty ($\pm$ 2$\sigma$)')
        else:
            # show 1 sigma
            ax.fill_between(epochs, np.percentile(diff,36,axis=0)*24*60, np.percentile(diff,64,axis=0)*24*60, alpha=0.2, color='k', label=r'Uncertainty ($\pm$ 1$\sigma$)')

        # duplicate axis and plot days since mid-transit
        ax2 = ax.twiny()
        ax2.set_xlabel(f"Time [BJD - {self.parameters['b']:.1f}]",fontsize=14)
        ax2.set_xlim(ax.get_xlim())
        xticks = ax.get_xticks()
        dt = np.round(xticks*self.parameters['m'],1)
        #ax2.set_xticks(dt)
        ax2.set_xticklabels(dt)

        if ylim == 'diff':
            ax.set_ylim([ min(np.percentile(diff,1,axis=0)*24*60),
                          max(np.percentile(diff,99,axis=0)*24*60)])

        # overlay the prior ephemeris
        if self.prior is not None:
            # create fill between area for uncertainty of old/prior ephemeris
            epochs_p = (np.linspace(self.data.min()-7, self.data.max()+7, 1000) - self.prior['b'][0])/self.prior['m'][0]
            prior = epochs_p*self.prior['m'][0] + self.prior['b'][0]
            mc_m_p = np.random.normal(self.prior['m'][0], self.prior['m'][1], size=10000)
            mc_b_p = np.random.normal(self.prior['b'][0], self.prior['b'][1], size=10000)
            mc_model_p = np.expand_dims(epochs_p,-1) * mc_m_p + mc_b_p
            diff_p = mc_model_p.T - model

            # plot an invisible line so the 2nd axes are happy
            ax2.plot(epochs, (model-prior)*24*60, ls='--', color='r', alpha=0)

            # why is this so small!?!?!? consistent to within machine precision?
            #ax.plot(epochs, (model-prior)*24*60, ls='--', color='r')

            if show_2sigma:
                ax.fill_between(epochs, np.percentile(diff_p,2,axis=0)*24*60, np.percentile(diff_p,98,axis=0)*24*60, alpha=0.1, color='r', label=r'Prior ($\pm$ 2$\sigma$)')
            else:
                # show ~1 sigma
                ax.fill_between(epochs, np.percentile(diff_p,36,axis=0)*24*60, np.percentile(diff_p,64,axis=0)*24*60, alpha=0.1, color='r', label=r'Prior ($\pm$ 1$\sigma$)')

            if ylim == 'prior':
                ax.set_ylim([ min(np.percentile(diff_p,1,axis=0)*24*60),
                            max(np.percentile(diff_p,99,axis=0)*24*60)])
            elif ylim == 'average':
                ax.set_ylim([ 0.5*(min(np.percentile(diff,1,axis=0)*24*60) + min(np.percentile(diff_p,1,axis=0)*24*60)),
                            0.5*(max(np.percentile(diff,99,axis=0)*24*60) + max(np.percentile(diff_p,99,axis=0)*24*60))])

        ax.axhline(0,color='black',alpha=0.5,ls='--',
                   label="Period: {:.7f}+-{:.7f} days\nT_mid: {:.7f}+-{:.7f} BJD".format(self.parameters['m'], self.errors['m'], self.parameters['b'], self.errors['b']))

        # TODO sig figs
        #lclabel2 = r"$T_{mid}$ = %s $\pm$ %s BJD$_{TDB}$" %(
        #    str(round_to_2(self.parameters['tmid'], self.errors.get('tmid',0))),
        #    str(round_to_2(self.errors.get('tmid',0)))
        #)

        ax.legend(loc='best')
        ax.set_xlabel("Epoch [number]",fontsize=14)
        ax.set_ylabel("Residuals [min]",fontsize=14)
        ax.grid(True, ls='--')
        return fig, ax

    def plot_triangle(self):
        """ Create a posterior triangle plot of the results. """
        ranges = []
        mask1 = np.ones(len(self.results['weighted_samples']['logl']),dtype=bool)
        mask2 = np.ones(len(self.results['weighted_samples']['logl']),dtype=bool)
        mask3 = np.ones(len(self.results['weighted_samples']['logl']),dtype=bool)
        titles = []
        labels= []
        flabels = {
            'm':'Period [day]',
            'b':'T_mid [JD]',
        }
        for i, key in enumerate(self.quantiles):
            labels.append(flabels.get(key, key))
            titles.append(f"{self.parameters[key]:.7f} +-\n {self.errors[key]:.7f}")

            # set the axes limits for the plots
            ranges.append([
                self.parameters[key] - 5*self.errors[key],
                self.parameters[key] + 5*self.errors[key]
            ])

            if key == 'a2' or key == 'a1':
                continue

            # create masks for contouring on sigma bounds
            mask3 = mask3 & (self.results['weighted_samples']['points'][:,i] > (self.parameters[key] - 3*self.errors[key]) ) & \
                (self.results['weighted_samples']['points'][:,i] < (self.parameters[key] + 3*self.errors[key]) )

            mask1 = mask1 & (self.results['weighted_samples']['points'][:,i] > (self.parameters[key] - self.errors[key]) ) & \
                (self.results['weighted_samples']['points'][:,i] < (self.parameters[key] + self.errors[key]) )

            mask2 = mask2 & (self.results['weighted_samples']['points'][:,i] > (self.parameters[key] - 2*self.errors[key]) ) & \
                (self.results['weighted_samples']['points'][:,i] < (self.parameters[key] + 2*self.errors[key]) )

        chi2 = self.results['weighted_samples']['logl']*-2
        fig = corner(self.results['weighted_samples']['points'],
            labels= labels,
            bins=int(np.sqrt(self.results['samples'].shape[0])),
            range= ranges,
            figsize=(10,10),
            #quantiles=(0.1, 0.84),
            plot_contours=True,
            levels=[ np.percentile(chi2[mask1],95), np.percentile(chi2[mask2],95), np.percentile(chi2[mask3],95)],
            plot_density=False,
            titles=titles,
            data_kwargs={
                'c':chi2, # color code by chi2
                'vmin':np.percentile(chi2[mask3],1),
                'vmax':np.percentile(chi2[mask3],95),
                'cmap':'viridis'
            },
            label_kwargs={
                'labelpad':50,
            },
            hist_kwargs={
                'color':'black',
            }
        )
        return fig

    def plot_periodogram(self):
        """ Search the residuals for periodic signals. """

        # compute a period range
        si = np.argsort(self.epochs)
        minper = max(2,2 * np.diff(self.epochs[si]).min())
        maxper = (np.max(self.epochs) - np.min(self.epochs))*3.

        # recompute on new grid
        ls = LombScargle(self.epochs, self.residuals, dy=self.dataerr)
        freq,power = ls.autopower(maximum_frequency=1./minper, minimum_frequency=1./maxper, nyquist_factor=2)

        # Phase fold data at max peak
        mi = np.argmax(power)
        per = 1./freq[mi]
        newphase = self.epochs/per % 1
        self.periods = 1./freq
        self.power = power

        # find best fit signal with 1 period
        # construct basis vectors with sin and cos
        basis = np.ones((3, len(self.epochs)))
        basis[0] = np.sin(2*np.pi*self.epochs/per)
        basis[1] = np.cos(2*np.pi*self.epochs/per)
        # fit for the coefficients with ordinary least squares
        #coeffs = np.linalg.lstsq(basis.T, self.residuals, rcond=None)[0]
        
        #perform the weighted least squares regression
        res = sm.WLS(self.residuals, basis.T, weights=1.0/self.dataerr**2).fit()
        coeffs = res.params #retrieve the slope and intercept of the fit from res
        y_bestfit = np.dot(basis.T, coeffs) # reconstruct signal

        # TODO use uncertainty to derive fill between region
        #std_dev = np.sqrt(np.diagonal(res.normalized_cov_params)) 


        # super sample fourier solution
        xnew = np.linspace(self.epochs.min(), self.epochs.max(), 1000)
        basis_new = np.ones((3, len(xnew)))
        basis_new[0] = np.sin(2*np.pi*xnew/per)
        basis_new[1] = np.cos(2*np.pi*xnew/per)
        y_bestfit_new = np.dot(basis_new.T, coeffs)

        # create plot
        fig, ax = plt.subplots(4, figsize=(10,14))

        # periodogram plot
        ax[0].semilogx(self.periods,self.power,'k-',label='Data')
        ax[0].set_xlabel("Period [epoch]",fontsize=14)
        ax[0].set_ylabel('Power',fontsize=14)
        ax[0].axvline(per,color='red',label=f'Period: {per:.2f}',alpha=0.5)
        ax[0].set_title("Lomb-Scargle Periodogram of O-C Data")
        ax[0].set_ylim([0,0.5*(self.power.max()+np.percentile(self.power,99))])

        # plot false alarm probability on lomb-scargle periodogram
        fp = ls.false_alarm_probability(power.max(), method='bootstrap')
        fp_levels = ls.false_alarm_level([0.01, 0.05, 0.1], method='bootstrap')

        # plot as horizontal line
        ax[0].axhline(fp_levels[0], color='red', ls='--', label='99% FAP')

        # o-c time series with fourier solution
        ax[1].errorbar(self.epochs,self.residuals*24*60,
                    yerr=self.dataerr*24*60,ls='none',
                    marker='.',color='black',
                    label=f'Data')
        ax[1].plot(xnew, y_bestfit_new*24*60, 'r-', label=f'Fourier Fit 1 (Period: {per:.2f})')
        ax[1].set_xlabel(f"Epochs",fontsize=14)
        ax[1].set_ylabel("O-C [min]",fontsize=14)
        ax[1].grid(True,ls='--')

        # phase folded time series with fourier solution
        ax[2].errorbar(newphase,self.residuals*24*60,
                    yerr=self.dataerr*24*60,ls='none',
                    marker='.',color='black',
                    label=f'Data')

        # super sample fourier solution
        newepochs = np.linspace(0,per, 1000)
        basis_new = np.ones((3, len(newepochs)))
        basis_new[0] = np.sin(2*np.pi*newepochs/per)
        basis_new[1] = np.cos(2*np.pi*newepochs/per)
        y_bestfit_new = np.dot(basis_new.T, coeffs)
        xnewphase = newepochs/per % 1

        ax[2].plot(xnewphase, y_bestfit_new*24*60, 'r.', ms=4, label=f'Fourier Fit 1')

        # sort data in phase
        si = np.argsort(newphase)
        # bin data into 8 bins
        bins = np.linspace(0,1,8)
        binned = np.zeros(len(bins))
        binned_std = np.zeros(len(bins))

        for i in range(0,len(bins)):
            mask = np.digitize(newphase[si], bins)==i
            if mask.sum() > 1:
                binned[i] = np.mean(self.residuals[si][mask])
                binned_std[i] = np.std(self.residuals[si][mask])
            elif mask.sum() == 1:
                binned[i] = self.residuals[si][mask]
                binned_std[i] = self.dataerr[si][mask]
            else:
                binned[i] = np.nan
                binned_std[i] = np.nan

        # plot binned data
        ax[2].errorbar(bins-0.5/len(bins),binned*24*60,
                    yerr=binned_std*24*60,ls='none',
                    marker='o',color='orange',
                    label=f'Binned Data')

        ax[2].set_xlabel(f"Phase (Period: {per:.2f} epochs)",fontsize=14)
        ax[2].set_ylabel("O-C [min]",fontsize=14)
        ax[2].grid(True,ls='--')

        # detrend data and fit again
        residuals = self.residuals - y_bestfit
        maxper = 50

        # find periodogram of residuals
        freq2,power2 = LombScargle(self.epochs, residuals, dy=self.dataerr).autopower(minimum_frequency=1./maxper, maximum_frequency=1./minper, nyquist_factor=2)

        # find max period
        mi2 = np.argmax(power2)
        per2 = 1./freq2[mi2]

        # find best fit signal with 2 periods
        # construct basis vectors with sin and cos
        basis2 = np.ones((5, len(self.epochs)))
        basis2[0] = np.sin(2*np.pi*self.epochs/per)
        basis2[1] = np.cos(2*np.pi*self.epochs/per)
        basis2[2] = np.sin(2*np.pi*self.epochs/per2)
        basis2[3] = np.cos(2*np.pi*self.epochs/per2)
        # fit for the coefficients
        #coeffs = np.linalg.lstsq(basis2.T, self.residuals, rcond=None)[0]
        #y_bestfit = np.dot(basis2.T, coeffs) # reconstruct signal
        #perform the weighted least squares regression
        res = sm.WLS(self.residuals, basis2.T, weights=1.0/self.dataerr**2).fit()
        coeffs = res.params #retrieve the slope and intercept of the fit from res
        y_bestfit = np.dot(basis2.T, coeffs)

        # super sample fourier solution
        xnew = np.linspace(self.epochs.min(), self.epochs.max(), 1000)
        basis_new = np.ones((5, len(xnew)))
        basis_new[0] = np.sin(2*np.pi*xnew/per)
        basis_new[1] = np.cos(2*np.pi*xnew/per)
        basis_new[2] = np.sin(2*np.pi*xnew/per2)
        basis_new[3] = np.cos(2*np.pi*xnew/per2)
        y_bestfit_new = np.dot(basis_new.T, coeffs)

        xnewphase = xnew/per2 % 1

        # plot detrended data
        ax[0].semilogx(1./freq2,power2,'k-',alpha=0.5,label='Detrended Data')
        ax[0].axvline(per2,color='cyan', alpha=0.5, label=f'Period: {per2:.2f}')
        ax[1].plot(xnew, y_bestfit_new*24*60, 'c-', alpha=0.5, label=f'Fourier Fit 2 (Period: {per:.2f} and {per2:.2f})')
        ax[0].legend(loc='best')
        ax[1].legend(loc='best')
        ax[2].legend(loc='best')

        newphase = self.epochs/per2 % 1
        ax[3].errorbar(newphase,residuals*24*60,
                    yerr=self.dataerr*24*60,ls='none',
                    marker='.',color='black',
                    label=f'Data - Fourier Fit 1')

        # create single sine wave from detrended data
        basis_new = np.ones((3, len(xnew)))
        basis_new[0] = np.sin(2*np.pi*xnew/per2)
        basis_new[1] = np.cos(2*np.pi*xnew/per2)
        y_bestfit_new = np.dot(basis_new.T, coeffs[2:])
        y_bestfit_new -= np.mean(y_bestfit_new)
        # if this doesn't do it then re-fit the residuals with a single sine wave
    
        ax[3].plot(xnewphase, y_bestfit_new*24*60, 'c.', ms=4, label=f'Fourier Fit 2')
        ax[3].set_xlabel(f"Phase (Period: {per2:.2f} epochs)",fontsize=14)
        ax[3].set_ylabel("Residuals [min]",fontsize=14)
        ax[3].grid(True,ls='--')

        # sort data in phase
        si = np.argsort(newphase)
        # bin data into 8 bins
        bins = np.linspace(0,1,8)
        binned = np.zeros(len(bins))
        binned_std = np.zeros(len(bins))

        for i in range(0,len(bins)):
            mask = np.digitize(newphase[si], bins)==i
            if mask.sum() > 1:
                binned[i] = np.mean(residuals[si][mask])
                binned_std[i] = np.std(residuals[si][mask])
            elif mask.sum() == 1:
                binned[i] = residuals[si][mask]
                binned_std[i] = self.dataerr[si][mask]
            else:
                binned[i] = np.nan
                binned_std[i] = np.nan

        ax[3].errorbar(bins-0.5/len(bins),binned*24*60,
                    yerr=binned_std*24*60,ls='none',
                    marker='o',color='orange',
                    label=f'Binned Residuals')
        ax[3].legend(loc='best')

        return fig,ax

def main():
    Tc = np.array([ # measured mid-transit times
    2456588.69897499, 2456593.73645465, 2456646.65419785,
       2456923.85589088, 2456971.73409754, 2458042.70521614,
       2458047.75761158, 2458095.62091434, 2458100.66454441,
       2453912.51471333, 2454461.86099   , 2455215.32701,
       2455530.3197    , 2456543.33866   , 2459854.549103  
    ])

    Tc_error = np.array([
    0.00237294, 0.00290445, 0.00647494, 0.00445833, 0.00477952,
       0.00310127, 0.00209248, 0.00099052, 0.00563974, 0.00054,
       0.00024   , 0.00015   , 0.00016   , 0.00028   , 0.000382 
    ])

    P = 2.5199412024  # orbital period for your target

    Tc_norm = Tc - Tc.min()  #normalize the data to the first observation
    #print(Tc_norm)
    orbit = np.rint(Tc_norm / P)  #number of orbits since first observation (rounded to nearest integer)
    #print(orbit)

    #make a n x 2 matrix with 1's in the first column and values of orbit in the second
    A = np.vstack([np.ones(len(Tc)), orbit]).T 

    #perform the weighted least squares regression
    res = sm.WLS(Tc, A, weights=1.0/Tc_error**2).fit() 
    #use sm.WLS for weighted LS, sm.OLS for ordinary LS, or sm.GLS for general LS

    params = res.params #retrieve the slope and intercept of the fit from res
    std_dev = np.sqrt(np.diagonal(res.normalized_cov_params)) 

    slope = params[1]
    slope_std_dev = std_dev[1]
    intercept = params[0]
    intercept_std_dev = std_dev[0]

    #print(res.summary())
    #print("Params =",params)
    #print("Error matrix =",res.normalized_cov_params)
    #print("Standard Deviations =",std_dev)

    print("Weighted Linear Least Squares Solution")
    print("T0 =",intercept,"+-",intercept_std_dev)
    print("P =",slope,"+-",slope_std_dev)

    # min and max values to search between for fitting
    bounds = {
        'm':[P-0.1, P+0.1],                 # orbital period
        'b':[intercept-0.1, intercept+0.1]  # mid-transit time
    }

    # used to plot red overlay in O-C figure
    prior = {
        'm':[slope, slope_std_dev],         # value from WLS (replace with literature value)
        'b':[intercept, intercept_std_dev]  # value from WLS (replace with literature value)
    }

    prior = {
        'm':[2.5199412024020322, 2.59214970054424e-06],
        'b':[2459854.54339036, 0.0024412]
    }

    lf = linear_fitter( Tc, Tc_error, bounds, prior=prior )

    lf.plot_triangle()
    plt.subplots_adjust(top=0.9,hspace=0.2,wspace=0.2)
    plt.savefig("posterior.png")
    plt.close()
    print("image saved to: posterior.png")

    fig,ax = lf.plot_oc()
    plt.tight_layout()
    plt.savefig("oc.png")
    plt.show()
    plt.close()
    print("image saved to: oc.png")

    fig,ax = lf.plot_periodogram()
    plt.tight_layout()
    plt.savefig("periodogram.png")
    plt.close()
    print("image saved to: periodogram.png")

if __name__ == "__main__":
    main()
