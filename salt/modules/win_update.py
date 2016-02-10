# -*- coding: utf-8 -*-
'''
Module for running windows updates.

:depends:   - win32com
        - win32con
        - win32api
        - pywintypes

.. versionadded:: 2014.7.0

Set windows updates to run by category. Default behavior is to install
all updates that do not require user interaction to complete.
Optionally set ``categories`` to a category of your choice to only
install certain updates. Default is to set to install all available but driver updates.
The following example will install all Security and Critical Updates,
and download but not install standard updates.

.. code-block:: bash

    salt '*' win_update.install_updates categories="['Critical Updates', 'Security Updates']"

You can also specify a number of features about the update to have a
fine grain approach to specific types of updates. These are the following
features/states of updates available for configuring:
.. code-block:: text
    'UI' - User interaction required, skipped by default
    'downloaded' - Already downloaded, included by default
    'present' - Present on computer, included by default
    'installed' - Already installed, skipped by default
    'reboot' - Reboot required, included by default
    'hidden' - Skip hidden updates, skipped by default
    'software' - Software updates, included by default
    'driver' - Driver updates, included by default

The following example installs all updates that don't require a reboot:
.. code-block:: bash

    salt '*' win_update.install_updates skips="[{'reboot':True}]"


Once installed Salt will return a similar output:

.. code-block:: bash

    2 : Windows Server 2012 Update (KB123456)
    4 : Internet Explorer Security Update (KB098765)
    2 : Malware Definition Update (KB321456)
    ...

The number at the beginning of the line is an OperationResultCode from the Windows Update Agent,
it's enumeration is described here: https://msdn.microsoft.com/en-us/library/windows/desktop/aa387095(v=vs.85).aspx.
The result code is then followed by the update name and its KB identifier.

'''
from __future__ import absolute_import

# Import Python libs
import logging
from salt.ext.six.moves import range
try:
    import win32com.client
    import pythoncom
    HAS_DEPENDENCIES = True
except ImportError:
    HAS_DEPENDENCIES = False

import salt.utils

log = logging.getLogger(__name__)


def __virtual__():
    '''
    Only works on Windows systems
    '''
    if salt.utils.is_windows() and HAS_DEPENDENCIES:
        return True
    return False


def _gather_update_categories(updateCollection):
    '''
    this is a convenience method to gather what categories of updates are available in any update
    collection it is passed. Typically though, the download_collection.
    Some known categories:
        Updates
        Windows 7
        Critical Updates
        Security Updates
        Update Rollups
    '''
    categories = []
    for i in range(updateCollection.Count):
        update = updateCollection.Item(i)
        for j in range(update.Categories.Count):
            name = update.Categories.Item(j).Name
            if name not in categories:
                log.debug('found category: {0}'.format(name))
                categories.append(name)
    return categories


class PyWinUpdater(object):
    def __init__(self, categories=None, skipUI=True, skipDownloaded=False,
            skipInstalled=True, skipReboot=False, skipPresent=False,
            skipSoftwareUpdates=False, skipDriverUpdates=False, skipHidden=True):
        log.debug('CoInitializing the pycom system')
        pythoncom.CoInitialize()

        self.skipUI = skipUI
        self.skipDownloaded = skipDownloaded
        self.skipInstalled = skipInstalled
        self.skipReboot = skipReboot
        self.skipPresent = skipPresent
        self.skipHidden = skipHidden

        self.skipSoftwareUpdates = skipSoftwareUpdates
        self.skipDriverUpdates = skipDriverUpdates

        #the list of categories that the user wants to be searched for.
        self.categories = categories

        #the list of categories that are present in the updates found.
        self.foundCategories = []
        #careful not to get those two confused.

        log.debug('dispatching update_session to keep the session object.')
        self.update_session = win32com.client.Dispatch('Microsoft.Update.Session')

        log.debug('update_session got. Now creating a win_searcher to seek out the updates')
        self.win_searcher = self.update_session.CreateUpdateSearcher()

        #list of updates that are applicable by current settings.
        self.download_collection = win32com.client.Dispatch('Microsoft.Update.UpdateColl')

        #list of updates to be installed.
        self.install_collection = win32com.client.Dispatch('Microsoft.Update.UpdateColl')

        #the object responsible for fetching the actual downloads.
        self.win_downloader = self.update_session.CreateUpdateDownloader()
        self.win_downloader.Updates = self.download_collection

        #the object responsible for the installing of the updates.
        self.win_installer = self.update_session.CreateUpdateInstaller()
        self.win_installer.Updates = self.install_collection

        #the results of the download process
        self.download_results = None

        #the results of the installation process
        self.install_results = None

        #search results from CreateUpdateSearcher()
        self.search_results = None

    def Search(self, searchString):
        try:
            log.debug('beginning search of the passed string: {0}'.format(searchString))
            self.search_results = self.win_searcher.Search(searchString)
            log.debug('search completed successfully.')
        except Exception as e:
            log.info('search for updates failed. {0}'.format(str(e)))
            return e

        log.debug('parsing results. {0} updates were found.'.format(
            str(self.search_results.Updates.Count)))

        try:
            #step through the list of the updates to ensure that the updates match the
            # features desired.
            for update in self.search_results.Updates:
                #this skipps an update if UI updates are not desired.
                if update.InstallationBehavior.CanRequestUserInput:
                    log.debug('Skipped update {0} - requests user input'.format(str(update)))
                    continue

                #if this update is already downloaded, it doesn't need to be in
                # the download_collection. so skipping it unless the user mandates re-download.
                if self.skipDownloaded and update.IsDownloaded:
                    log.debug('Skipped update {0} - already downloaded'.format(str(update)))
                    continue

                #check this update's categories against the ones desired.
                for category in update.Categories:
                    #this is a zero guard. these tests have to be in this order
                    # or it will error out when the user tries to search for
                    # updates with out specifying categories.
                    if self.categories is None or category.Name in self.categories:
                        #adds it to the list to be downloaded.
                        self.download_collection.Add(update)
                        log.debug('added update {0}'.format(str(update)))
                        #ever update has 2 categories. this prevents the
                        #from being added twice.
                        break
            log.debug('download_collection made. gathering found categories.')

            #gets the categories of the updates available in this collection of updates
            self.foundCategories = _gather_update_categories(self.download_collection)
            log.debug('found categories: {0}'.format(str(self.foundCategories)))
            return True
        except Exception as e:
            log.info('parsing updates failed. {0}'.format(str(e)))
            return e

    def AutoSearch(self):
        '''
        this function generates a search string. simplifying the search function while
        still providing as many features as possible.
        '''
        search_string = ''
        searchParams = []

        if self.skipInstalled:
            searchParams.append('IsInstalled=0')
        else:
            searchParams.append('IsInstalled=1')

        if self.skipHidden:
            searchParams.append('IsHidden=0')
        else:
            searchParams.append('IsHidden=1')

        if self.skipReboot:
            searchParams.append('RebootRequired=0')
        else:
            searchParams.append('RebootRequired=1')

        if self.skipPresent:
            searchParams.append('IsPresent=0')
        else:
            searchParams.append('IsPresent=1')

        for i in searchParams:
            search_string += '{0} and '.format(i)

        if not self.skipSoftwareUpdates and not self.skipDriverUpdates:
            search_string += 'Type=\'Software\' or Type=\'Driver\''
        elif not self.skipSoftwareUpdates:
            search_string += 'Type=\'Software\''
        elif not self.skipDriverUpdates:
            search_string += 'Type=\'Driver\''
        else:
            return False
            #if there is no type, the is nothing to search.
        log.debug('generated search string: {0}'.format(search_string))
        return self.Search(search_string)

    def Download(self):
        #chase the download_collection! do the actual download process.
        try:
            #if the download_collection is empty. no need to download things.
            if self.download_collection.Count != 0:
                self.download_results = self.win_downloader.Download()
            else:
                log.debug('Skipped downloading, all updates were already cached.')
            return True
        except Exception as e:
            log.debug('failed in the downloading {0}.'.format(str(e)))
            return e

    def Install(self):
        #beat those updates into place!
        try:
            #this does not draw from the download_collection. important thing to know.
            #the blugger is created regardless of what the download_collection has done. but it
            #will only download those updates which have been downloaded and are ready.
            for update in self.search_results.Updates:
                if update.IsDownloaded:
                    self.install_collection.Add(update)
            log.debug('Updates prepared. beginning installation')
        except Exception as e:
            log.info('Preparing install list failed: {0}'.format(str(e)))
            return e

        #if the blugger is empty. no point it starting the install process.
        if self.install_collection.Count != 0:
            log.debug('Install list created, about to install')
            try:
                #the call to install.
                self.install_results = self.win_installer.Install()
                log.info('Installation of updates complete')
                return True
            except Exception as e:
                log.info('Installation failed: {0}'.format(str(e)))
                return e
        else:
            log.info('no new updates.')
            return True

    def GetInstallationResults(self):
        '''
        this gets results of installation process.
        '''
        #if the blugger is empty, the results are nil.
        log.debug('blugger has {0} updates in it'.format(str(self.install_collection.Count)))
        if self.install_collection.Count == 0:
            return {}

        updates = []
        log.debug('repairing update list')
        for i in range(self.install_collection.Count):
            #this gets the result from install_results, but the title comes from the update
            #collection install_collection.
            updates.append('{0}: {1}'.format(
                str(self.install_results.GetUpdateResult(i).ResultCode),
                str(self.install_collection.Item(i).Title)))

        log.debug('Update results enumerated, now making a library to pass back')
        results = {}

        #translates the list of update results into a library that salt expects.
        for i, update in enumerate(updates):
            results['update {0}'.format(i)] = update

        log.debug('Update information complied. returning')
        return results

    def GetInstallationResultsPretty(self):
        '''
        converts the installation results into a pretty print.
        '''
        updates = self.GetInstallationResults()
        ret = 'The following are the updates and their return codes.\n'
        for i in updates:
            ret += '\t{0}\n'.format(updates[i])
        return ret

    def GetDownloadResults(self):
        updates = []
        for i in range(self.download_collection.Count):
            updates.append('{0}: {1}'.format(
                str(self.download_results.GetUpdateResult(i).ResultCode),
                str(self.download_collection.Item(i).Title)))
        results = {}
        for i, update in enumerate(updates):
            results['update {0}'.format(i)] = update
        return results

    def GetSearchResults(self):
        updates = []
        log.debug('parsing results. {0} updates were found.'.format(
            str(self.download_collection.count)))

        for update in self.download_collection:
            if update.InstallationBehavior.CanRequestUserInput:
                log.debug('Skipped update {0}'.format(str(update)))
                continue
            updates.append(salt.utils.locales.sdecode(update))
            log.debug('added update {0}'.format(str(update)))
        return updates

    def GetSearchResultsPretty(self):
        updates = self.GetSearchResults()
        ret = 'There are {0} updates. they are as follows:\n'.format(str(len(updates)))
        for update in updates:
            ret += '\t{0}\n'.format(str(update))
        return ret

    def SetCategories(self, categories):
        self.categories = categories

    def GetCategories(self):
        return self.categories

    def GetAvailableCategories(self):
        return self.foundCategories

    def SetSkips(self, skips):
        if skips:
            for i in skips:
                value = i[next(i.iterkeys())]
                skip = next(i.iterkeys())
                self.SetSkip(skip, value)
                log.debug('was asked to set {0} to {1}'.format(skip, value))

    def SetSkip(self, skip, state):
        if skip == 'UI':
            self.skipUI = state
        elif skip == 'downloaded':
            self.skipDownloaded = state
        elif skip == 'installed':
            self.skipInstalled = state
        elif skip == 'reboot':
            self.skipReboot = state
        elif skip == 'present':
            self.skipPresent = state
        elif skip == 'hidden':
            self.skipHidden = state
        elif skip == 'software':
            self.skipSoftwareUpdates = state
        elif skip == 'driver':
            self.skipDriverUpdates = state
        log.debug('new search state: \n\tUI: {0}\n\tDownload: {1}\n\tInstalled: {2}\n\treboot :{3}\n\tPresent: {4}\n\thidden: {5}\n\tsoftware: {6}\n\tdriver: {7}'.format(
            self.skipUI, self.skipDownloaded, self.skipInstalled, self.skipReboot,
            self.skipPresent, self.skipHidden, self.skipSoftwareUpdates, self.skipDriverUpdates))

    def __str__(self):
        results = 'There are {0} updates, by category there are:\n'.format(
            str(self.download_collection.count))
        for category in self.foundCategories:
            count = 0
            for update in self.download_collection:
                for c in update.Categories:
                    if category == c.Name:
                        count += 1
            results += '\t{0}: {1}\n'.format(category, count)
        return results


def _search(quidditch, retries=5):
    '''
    a wrapper method for the pywinupdater class. I might move this into the class, but right now,
    that is to much for one class I think.
    '''
    passed = False
    clean = True
    comment = ''
    while not passed:
        log.debug('Searching. tries left: {0}'.format(str(retries)))
        #let the updater make its own search string. MORE POWER this way.
        passed = quidditch.AutoSearch()
        log.debug('Done searching: {0}'.format(str(passed)))
        if isinstance(passed, Exception):
            clean = False
            comment += 'Failed in the seeking/parsing process:\n\t\t{0}\n'.format(str(passed))
            retries -= 1
            if retries:
                comment += '{0} tries to go. retrying\n'.format(str(retries))
            else:
                comment += 'out of retries. this update round failed.\n'
                return (comment, True, retries)
            passed = False
    if clean:
        #bragging rights.
        comment += 'Search was done with out an error.\n'

    return (comment, True, retries)


def _download(quidditch, retries=5):
    '''
    another wrapper method.
    '''
    passed = False
    clean = True
    comment = ''
    while not passed:
        log.debug('Downloading. tries left: {0}'.format(str(retries)))
        passed = quidditch.Download()
        log.debug('Done downloading: {0}'.format(str(passed)))
        if isinstance(passed, Exception):
            clean = False
            comment += 'Failed while trying to download updates:\n\t\t{0}\n'.format(str(passed))
            retries -= 1
            if retries:
                comment += '{0} tries to go. retrying\n'.format(str(retries))
                passed = False
            else:
                comment += 'out of retries. this update round failed.\n'
                return (comment, False, retries)
    if clean:
        comment += 'Download was done without error.\n'
    return (comment, True, retries)


def _install(quidditch, retries=5):
    '''
    and the last wrapper method. keeping things simple.
    '''
    passed = False
    clean = True
    comment = ''
    while not passed:
        log.debug('download_collection is this long: {0}'.format(str(quidditch.install_collection.Count)))
        log.debug('Installing. tries left: {0}'.format(str(retries)))
        passed = quidditch.Install()
        log.info('Done installing: {0}'.format(str(passed)))
        if isinstance(passed, Exception):
            clean = False
            comment += 'Failed while trying to install the updates.\n\t\t{0}\n'.format(str(passed))
            retries -= 1
            if retries:
                comment += '{0} tries to go. retrying\n'.format(str(retries))
                passed = False
            else:
                comment += 'out of retries. this update round failed.\n'
                return (comment, False, retries)
    if clean:
        comment += 'Install was done without error.\n'
    return (comment, True, retries)


#this is where the actual functions available to salt begin.


def list_updates(verbose=False, skips=None, retries=5, categories=None):
    '''
    Returns a summary of available updates, grouped into their non-mutually
    exclusive categories.

    verbose
        Print results in greater detail

    retries
        Number of retries to make before giving up. This is total, not per
        step.

    categories
        Specify the categories to list. Must be passed as a list.

        .. code-block:: bash

            salt '*' win_update.list_updates categories="['Updates']"

        Categories include the following:

        * Updates
        * Windows 7
        * Critical Updates
        * Security Updates
        * Update Rollups

    CLI Examples:

    .. code-block:: bash

        # Normal Usage
        salt '*' win_update.list_updates

        # List all critical updates list in verbose detail
        salt '*' win_update.list_updates categories=['Critical Updates'] verbose=True

    '''

    log.debug('categories to search for are: {0}'.format(str(categories)))
    quidditch = PyWinUpdater()
    if categories:
        quidditch.SetCategories(categories)
    quidditch.SetSkips(skips)

    #this is where we be seeking the things! yar!
    comment, passed, retries = _search(quidditch, retries)
    if not passed:
        return (comment, str(passed))
    log.debug('verbose: {0}'.format(str(verbose)))
    if verbose:
        return str(quidditch.GetSearchResultsPretty())
    return str(quidditch)


def download_updates(skips=None, retries=5, categories=None):
    '''
    Downloads all available updates, skipping those that require user
    interaction.

    Various aspects of the updates can be included or excluded. this feature is
    still in development.

    retries
        Number of retries to make before giving up. This is total, not per
        step.

    categories
        Specify the categories to update. Must be passed as a list.

        .. code-block:: bash

            salt '*' win_update.download_updates categories="['Updates']"

        Categories include the following:

        * Updates
        * Windows 7
        * Critical Updates
        * Security Updates
        * Update Rollups

    CLI Examples:

    .. code-block:: bash

        # Normal Usage
        salt '*' win_update.download_updates

        # Download critical updates only
        salt '*' win_update.download_updates categories="['Critical Updates']"

    '''

    log.debug('categories to search for are: {0}'.format(str(categories)))
    quidditch = PyWinUpdater(skipDownloaded=True)
    quidditch.SetCategories(categories)
    quidditch.SetSkips(skips)

    ##this is where we be seeking the things! yar!
    comment, passed, retries = _search(quidditch, retries)
    if not passed:
        return (comment, str(passed))

    ##this is where we get all the things! i.e. download updates.
    comment, passed, retries = _download(quidditch, retries)
    if not passed:
        return (comment, str(passed))

    try:
        comment = quidditch.GetDownloadResults()
    except Exception as e:
        comment = 'could not get results, but updates were installed. {0}'.format(str(e))
    return 'Windows is up to date. \n{0}'.format(comment)


def install_updates(skips=None, retries=5, categories=None):
    '''
    Downloads and installs all available updates, skipping those that require
    user interaction.

    Add ``cached`` to only install those updates which have already been downloaded.

    you can set the maximum number of retries to ``n`` in the search process by
    adding: ``retries=n``

    various aspects of the updates can be included or excluded. This function is
    still under development.

    retries
        Number of retries to make before giving up. This is total, not per
        step.

    categories
        Specify the categories to install. Must be passed as a list.

        .. code-block:: bash

            salt '*' win_update.install_updates categories="['Updates']"

        Categories include the following:

        * Updates
        * Windows 7
        * Critical Updates
        * Security Updates
        * Update Rollups

    CLI Examples:

    .. code-block:: bash

        # Normal Usage
        salt '*' win_update.install_updates

        # Install all critical updates
        salt '*' win_update.install_updates categories="['Critical Updates']"

    '''

    log.debug('categories to search for are: {0}'.format(str(categories)))
    quidditch = PyWinUpdater()
    quidditch.SetCategories(categories)
    quidditch.SetSkips(skips)

    ##this is where we be seeking the things! yar!
    comment, passed, retries = _search(quidditch, retries)
    if not passed:
        return (comment, str(passed))

    ##this is where we get all the things! i.e. download updates.
    comment, passed, retries = _download(quidditch, retries)
    if not passed:
        return (comment, str(passed))

    ##this is where we put things in their place!
    comment, passed, retries = _install(quidditch, retries)
    if not passed:
        return (comment, str(passed))

    try:
        comment = quidditch.GetInstallationResultsPretty()
    except Exception as e:
        comment = 'Could not get results, but updates were installed. {0}'.format(str(e))
    return 'Windows is up to date. \n{0}'.format(comment)
