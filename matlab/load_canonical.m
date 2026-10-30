function [EEG, events] = load_canonical(vhdr, varargin)
%LOAD_CANONICAL Load an eegunify canonical recording (BIDS BrainVision) into MATLAB.
%
%   [EEG, events] = load_canonical('rawdata/sub-015/ses-N170/eeg/sub-015_ses-N170_task-N170_eeg.vhdr')
%   data          = load_canonical(vhdr, 'toolbox', 'fieldtrip')
%
%   EEGLAB (default): returns an EEGLAB EEG struct whose events come from the BIDS
%   events.tsv (the authoritative event list): EEG.event(k).type is the trial_type
%   (e.g. 'stimulus/face') and EEG.event(k).code the original trigger code. Bad channels
%   from channels.tsv are listed in EEG.etc.eegunify.bad_channels (not removed).
%   FieldTrip: returns the ft_preprocessing output plus the same events table.
%
%   Requires EEGLAB on the path (e.g. via Practice/setup_eeg_toolboxes.m). The BrainVision
%   file is read with EEGLAB's bva-io plugin if installed, otherwise with FieldTrip's
%   ft_read_header/ft_read_data. Data are in microvolts, as stored on disk.

p = inputParser;
addRequired(p, 'vhdr', @(x) ischar(x) || isstring(x));
addParameter(p, 'toolbox', 'eeglab', @(x) any(strcmpi(x, {'eeglab', 'fieldtrip'})));
parse(p, vhdr, varargin{:});
vhdr = char(p.Results.vhdr);

[folder, name, ext] = fileparts(vhdr);
assert(strcmpi(ext, '.vhdr'), 'load_canonical: expected a .vhdr file, got %s', ext);
assert(isfile(vhdr), 'load_canonical: %s not found', vhdr);
base = regexprep(name, '_eeg$', '');
events = read_tsv(fullfile(folder, [base '_events.tsv']));
channels = read_tsv(fullfile(folder, [base '_channels.tsv']));

if strcmpi(p.Results.toolbox, 'fieldtrip')
    cfg = struct('dataset', vhdr);
    EEG = ft_preprocessing(cfg);
    return
end

if exist('pop_loadbv', 'file') == 2
    EEG = pop_loadbv(folder, [name ext]);
else
    EEG = read_with_fieldtrip(vhdr);
end
% Replace the marker-file events with events.tsv (onset in seconds, BIDS 0-based samples).
n = height(events);
ev = struct('type', cell(1, n), 'latency', [], 'duration', [], 'code', []);
for k = 1:n
    ev(k).type = char(string(events.trial_type(k)));
    ev(k).latency = events.onset(k) * EEG.srate + 1;          % EEGLAB latencies are 1-based
    ev(k).duration = max(0, double(events.duration(k))) * EEG.srate;
    ev(k).code = char(string(events.value(k)));
end
EEG.event = ev;
EEG.urevent = [];  % events.tsv is the record; no separate backup event list
EEG = eeg_checkset(EEG, 'eventconsistency');

isbad = strcmp(string(channels.status), "bad");
EEG.etc.eegunify.bad_channels = cellstr(string(channels.name(isbad)))';
EEG.etc.eegunify.source = vhdr;
end

function EEG = read_with_fieldtrip(vhdr)
assert(exist('ft_read_header', 'file') == 2, ...
    'load_canonical: needs EEGLAB''s bva-io plugin or FieldTrip on the path');
hdr = ft_read_header(vhdr);
dat = ft_read_data(vhdr, 'header', hdr);
EEG = eeg_emptyset();
EEG.setname = 'eegunify canonical';
EEG.data = double(dat);
[EEG.nbchan, EEG.pnts] = size(EEG.data);
EEG.trials = 1;
EEG.srate = hdr.Fs;
EEG.xmin = 0;
EEG.xmax = (EEG.pnts - 1) / EEG.srate;
EEG.chanlocs = struct('labels', hdr.label(:)');
EEG.ref = 'as recorded (see EEGReference in _eeg.json)';
end

function t = read_tsv(path)
assert(isfile(path), 'load_canonical: %s not found', path);
opts = detectImportOptions(path, 'FileType', 'text', 'Delimiter', '\t');
opts = setvartype(opts, intersect(opts.VariableNames, {'value', 'trial_type', 'name', ...
    'status', 'type', 'units'}), 'string');
opts.MissingRule = 'fill';
t = readtable(path, opts);
end
