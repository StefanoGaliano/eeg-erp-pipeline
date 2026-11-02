function erplab_path()
%ERPLAB_PATH  Put ERPLAB's subfolders on the path (does nothing when they are there).
%
%   'eeglab nogui' adds only each plugin's top folder and skips eegplugin_erplab, which is
%   what adds ERPLAB's subfolders (pop_functions, functions, ...). Do that here, then keep
%   EEGLAB's icadefs in front of ERPLAB's copy, as eeglab.m does with its GUI.
if ~isempty(which('pop_creabasiceventlist')), return, end
erplabdir = fileparts(which('eegplugin_erplab'));
if isempty(erplabdir)
    error('eegunify:noERPLAB', 'ERPLAB not found: run eeglab with ERPLAB installed');
end
addpath(genpath(erplabdir));
icadefsdir = fileparts(which('icadefs'));
if contains(lower(icadefsdir), 'erplab')
    rmpath(icadefsdir);
    if isempty(which('icadefs')), addpath(icadefsdir); end
end
end
