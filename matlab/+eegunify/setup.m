function setup(setupScript)
%SETUP  Put EEGLAB and FieldTrip on the path for the eegunify MATLAB package.
%
%   eegunify.setup()                      % uses EEGUNIFY_MATLAB_SETUP
%   eegunify.setup('.../Practice/setup_eeg_toolboxes.m')
%
%   Runs the workspace's toolbox setup script (plain addpath of the toolbox top folders,
%   never genpath, which shadows functions). Does nothing when EEGLAB and FieldTrip are
%   already on the path.
if exist('pop_loadset', 'file') == 2 && exist('ft_read_header', 'file') == 2
    return
end
if nargin < 1 || isempty(setupScript)
    setupScript = getenv('EEGUNIFY_MATLAB_SETUP');
end
assert(~isempty(setupScript) && isfile(setupScript), 'eegunify:setup', ...
    'set EEGUNIFY_MATLAB_SETUP to the toolbox setup script (setup_eeg_toolboxes.m)');
[folder, name] = fileparts(setupScript);
here = pwd;
back = onCleanup(@() cd(here));
cd(folder);
evalc(name);  % the script prints versions; keep the console quiet
end
