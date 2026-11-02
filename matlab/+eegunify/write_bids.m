function vhdr = write_bids(rec, cfg, bidsRoot)
%WRITE_BIDS  Write a canonical recording as BIDS-EEG (BrainVision float32, µV).
%
%   vhdr = eegunify.write_bids(rec, cfg, bidsRoot)
%
%   A hand-written writer (no bids-matlab-tools), so the files are exactly what the Python
%   side compares: <base>_eeg.{vhdr,vmrk,eeg} (multiplexed IEEE float32, resolution 1 µV),
%   _channels.tsv, _events.tsv and _eeg.json with an "eegunify" block that carries the
%   config_hash. No electrodes.tsv (positions are outside Phase M).
c = cfg.config;
sub = cfg.subject;
parts = {['sub-' sub]};
folder = fullfile(bidsRoot, ['sub-' sub]);
if ~isempty(c.recordings.session)
    parts{end + 1} = ['ses-' c.recordings.session];
    folder = fullfile(folder, ['ses-' c.recordings.session]);
end
parts{end + 1} = ['task-' c.dataset.task];
if isfield(c.recordings, 'run') && ~isempty(c.recordings.run)
    parts{end + 1} = sprintf('run-%s', num2str(c.recordings.run));
end
base = strjoin(parts, '_');
folder = fullfile(folder, 'eeg');
if ~isfolder(folder), mkdir(folder); end
vhdr = fullfile(folder, [base '_eeg.vhdr']);

write_dataset_description(bidsRoot, c);
write_brainvision(folder, base, rec);
write_channels(fullfile(folder, [base '_channels.tsv']), rec);
write_events(fullfile(folder, [base '_events.tsv']), rec.events, rec.srate);
write_sidecar(fullfile(folder, [base '_eeg.json']), rec, cfg);
end

function write_brainvision(folder, base, rec)
eegName = [base '_eeg.eeg'];
vmrkName = [base '_eeg.vmrk'];
fid = fopen(fullfile(folder, eegName), 'w', 'ieee-le');
fwrite(fid, single(rec.data), 'single');  % column-major (channels x samples) = multiplexed
fclose(fid);

h = {'Brain Vision Data Exchange Header File Version 1.0'; '; Written by the eegunify MATLAB package'
    ''; '[Common Infos]'; 'Codepage=UTF-8'; ['DataFile=' eegName]; ['MarkerFile=' vmrkName]
    'DataFormat=BINARY'; 'DataOrientation=MULTIPLEXED'
    sprintf('NumberOfChannels=%d', numel(rec.names))
    sprintf('SamplingInterval=%.10g', 1e6 / rec.srate)
    ''; '[Binary Infos]'; 'BinaryFormat=IEEE_FLOAT_32'; ''; '[Channel Infos]'};
h = h(:);
for k = 1:numel(rec.names)
    h{end + 1} = sprintf('Ch%d=%s,,1,%sV', k, strrep(rec.names{k}, ',', '\1'), char(181)); %#ok<AGROW>
end
write_lines(fullfile(folder, [base '_eeg.vhdr']), h);

m = {'Brain Vision Data Exchange Marker File Version 1.0'; ''; '[Common Infos]'
    'Codepage=UTF-8'; ['DataFile=' eegName]; ''; '[Marker Infos]'; 'Mk1=New Segment,,1,1,0'};
m = m(:);
ev = rec.events;
for k = 1:height(ev)
    m{end + 1} = sprintf('Mk%d=Comment,%s,%d,1,0', k + 1, ...
        strrep(ev.trial_type{k}, ',', '\1'), ev.sample(k) + 1); %#ok<AGROW>
end
write_lines(fullfile(folder, vmrkName), m);
end

function write_channels(path, rec)
bidsType = containers.Map({'eeg', 'eog', 'emg', 'ecg', 'misc', 'resp', 'temperature'}, ...
    {'EEG', 'EOG', 'EMG', 'ECG', 'MISC', 'RESP', 'TEMP'});
desc = containers.Map({'eeg', 'eog', 'emg', 'ecg', 'misc', 'resp', 'temperature'}, ...
    {'ElectroEncephaloGram', 'ElectroOculoGram', 'ElectroMyoGram', 'ElectroCardioGram', ...
    'Miscellaneous', 'Respiration', 'Temperature'});
lines = {strjoin({'name', 'type', 'units', 'low_cutoff', 'high_cutoff', 'description', ...
    'sampling_frequency', 'status', 'status_description', 'original_name'}, '\t')};
for k = 1:numel(rec.names)
    status = 'good';
    if ismember(rec.names{k}, rec.bads), status = 'bad'; end
    lines{end + 1} = sprintf('%s\t%s\tµV\tn/a\tn/a\t%s\t%.10g\t%s\tn/a\t%s', rec.names{k}, ...
        bidsType(rec.types{k}), desc(rec.types{k}), rec.srate, status, rec.original{k}); %#ok<AGROW>
end
write_lines(path, lines);
end

function write_events(path, ev, srate)
% Onsets are written as sample / srate, as MNE-BIDS does on the Python side (a shifted onset
% falls between samples; the sample is the record).
lines = {sprintf('onset\tduration\ttrial_type\tvalue\tsample')};
for k = 1:height(ev)
    lines{end + 1} = sprintf('%.17g\t%.17g\t%s\t%s\t%d', ev.sample(k) / srate, ev.duration(k), ...
        ev.trial_type{k}, ev.value{k}, ev.sample(k)); %#ok<AGROW>
end
write_lines(path, lines);
end

function write_sidecar(path, rec, cfg)
c = cfg.config;
s = struct();
s.TaskName = c.dataset.task;
s.SamplingFrequency = rec.srate;
s.PowerLineFrequency = 'n/a';
if isfield(rec.sidecar, 'PowerLineFrequency') && isnumeric(rec.sidecar.PowerLineFrequency)
    s.PowerLineFrequency = rec.sidecar.PowerLineFrequency;
end
s.SoftwareFilters = 'n/a';
s.RecordingDuration = (size(rec.data, 2) - 1) / rec.srate;
s.RecordingType = 'continuous';
s.EEGReference = rec.reference;
s.EEGGround = 'n/a';
if ~isempty(c.reference.ground), s.EEGGround = c.reference.ground; end
for t = {'EEG', 'EOG', 'ECG', 'EMG'}
    s.([t{1} 'ChannelCount']) = sum(strcmpi(rec.types, t{1}));
end
s.MiscChannelCount = sum(strcmp(rec.types, 'misc'));
s.TriggerChannelCount = 0;
s.Manufacturer = rec.manufacturer;
for k = cfg.matlab.inherited_sidecar_keys
    if isfield(rec.sidecar, k{1}), s.(k{1}) = rec.sidecar.(k{1}); end
end
files = struct('name', {}, 'sha256', {});
for k = 1:numel(rec.source_files)
    [~, n, e] = fileparts(rec.source_files{k});
    files(k) = struct('name', [n e], 'sha256', eegunify.sha256(rec.source_files{k}));
end
s.eegunify = struct('implementation', 'matlab', 'version', cfg.eegunify_version, ...
    'config_hash', cfg.config_hash, 'source_format', rec.format, 'reader', rec.reader, ...
    'source_files', files, 'event_extraction', rec.event_method, ...
    'unit_correction_factor', rec.unit_factor);
fid = fopen(path, 'w', 'n', 'UTF-8');
fwrite(fid, jsonencode(s, 'PrettyPrint', true), 'char');
fprintf(fid, '\n');
fclose(fid);
end

function write_dataset_description(root, c)
path = fullfile(root, 'dataset_description.json');
if isfile(path), return, end
if ~isfolder(root), mkdir(root); end
d = struct('Name', c.dataset.name, 'BIDSVersion', '1.9.0', 'DatasetType', 'raw', ...
    'GeneratedBy', {{struct('Name', 'eegunify (MATLAB)')}});
if ~isempty(c.dataset.license), d.License = c.dataset.license; end
fid = fopen(path, 'w', 'n', 'UTF-8');
fwrite(fid, jsonencode(d, 'PrettyPrint', true), 'char');
fclose(fid);
end

function write_lines(path, lines)
fid = fopen(path, 'w', 'n', 'UTF-8');
assert(fid > 0, 'eegunify:write', 'cannot write %s', path);
fprintf(fid, '%s\n', lines{:});
fclose(fid);
end
