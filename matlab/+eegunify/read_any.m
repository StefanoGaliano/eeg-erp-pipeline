function rec = read_any(path, fmt, hints)
%READ_ANY  Read a BrainVision, BDF or EEGLAB file into a plain struct (Phase M readers).
%
%   rec = eegunify.read_any(path)            % format from the extension
%   rec = eegunify.read_any(path, 'bdf', hints)
%
%   rec fields: data (channels x samples, double, µV), labels, reader_types ('eeg' or
%   'stim'), srate, markers (struct array: sample (0-based), duration (s), description),
%   status_mask, source_files, reader, manufacturer, default_reference.
%
%   Readers: EEGLAB .set with pop_loadset; BrainVision and BDF with FieldTrip
%   ft_read_header/ft_read_data (BDF scaling cal*digital+offset, as in MNE). BrainVision
%   markers are parsed from the .vmrk here so their description is exactly MNE's
%   "<type>/<description>"; BDF events come from the Status channel (extract_events).
if nargin < 2 || isempty(fmt) || strcmp(fmt, 'auto')
    fmt = detect_format(path);
end
if nargin < 3, hints = struct(); end
assert(isfile(path), 'eegunify:read', '%s not found', path);
rec = struct('data', [], 'labels', {{}}, 'reader_types', {{}}, 'srate', NaN, ...
    'markers', struct('sample', {}, 'duration', {}, 'description', {}), ...
    'status_mask', 16777215, 'source_files', {{path}}, 'reader', '', ...
    'manufacturer', 'n/a', 'default_reference', '', 'format', fmt);
switch fmt
    case 'eeglab'
        rec = read_set(rec, path);
    case 'brainvision'
        rec = read_brainvision(rec, path);
    case 'bdf'
        rec = read_bdf(rec, path, hints);
    otherwise
        error('eegunify:read', ['format %s is not in the MATLAB package (Phase M covers ' ...
            'brainvision, bdf and eeglab)'], fmt);
end
end

function fmt = detect_format(path)
[~, ~, ext] = fileparts(path);
switch lower(ext)
    case '.vhdr', fmt = 'brainvision';
    case '.bdf', fmt = 'bdf';
    case '.set', fmt = 'eeglab';
    otherwise, error('eegunify:read', 'unknown extension %s', ext);
end
end

function rec = read_set(rec, path)
[folder, name, ext] = fileparts(path);
EEG = pop_loadset('filename', [name ext], 'filepath', folder, 'verbose', 'off');
assert(EEG.trials == 1, 'eegunify:read', '%s is epoched; expected continuous data', path);
rec.data = double(EEG.data);  % EEGLAB stores µV
rec.labels = {EEG.chanlocs.labels};
rec.reader_types = repmat({'eeg'}, 1, EEG.nbchan);
rec.srate = EEG.srate;
rec.reader = 'eeglab pop_loadset';
fdt = fullfile(folder, [name '.fdt']);
if isfile(fdt), rec.source_files{end + 1} = fdt; end
ev = EEG.event;
for k = 1:numel(ev)
    lat = ev(k).latency;
    if isempty(lat) || isnan(lat), continue, end
    dur = 0;
    if isfield(ev, 'duration') && ~isempty(ev(k).duration), dur = ev(k).duration; end
    % MNE: onset = (latency - 1) / srate, duration in samples / srate.
    rec.markers(end + 1) = struct('sample', lat - 1, 'duration', dur / EEG.srate, ...
        'description', code_str(ev(k).type));
end
end

function s = code_str(t)
% str() of the value MNE reads: numbers without trailing zeros, text as is.
if isnumeric(t)
    if t == round(t), s = sprintf('%d', t); else, s = sprintf('%g', t); end
else
    s = char(t);
end
end

function rec = read_brainvision(rec, path)
hdr = ft_read_header(path, 'headerformat', 'brainvision_vhdr');
dat = ft_read_data(path, 'header', hdr, 'dataformat', 'brainvision_eeg');
rec.data = double(dat) .* to_uv(hdr);
rec.labels = hdr.label(:)';
rec.reader_types = repmat({'eeg'}, 1, numel(rec.labels));
rec.srate = hdr.Fs;
rec.reader = 'fieldtrip ft_read_data (brainvision)';
rec.manufacturer = 'Brain Products';
[folder, ~] = fileparts(path);
info = ini_values(path);
for f = {'DataFile', 'MarkerFile'}
    if isfield(info, f{1}), rec.source_files{end + 1} = fullfile(folder, info.(f{1})); end
end
vmrk = fullfile(folder, info.MarkerFile);
% MNE reads the vmrk the same way: commas inside fields are coded as \1.
lines = splitlines(fileread(vmrk));
for k = 1:numel(lines)
    tok = regexp(lines{k}, '^Mk\d+=([^,]*),([^,]*),(\d+),(\d+)', 'tokens', 'once');
    if isempty(tok), continue, end
    desc = strrep([tok{1} '/' tok{2}], '\1', ',');  % MNE: "Stimulus/S  1"
    rec.markers(end + 1) = struct('sample', str2double(tok{3}) - 1, ...
        'duration', str2double(tok{4}) / hdr.Fs, 'description', desc);
end
end

function s = ini_values(vhdr)
s = struct();
for l = splitlines(fileread(vhdr))'
    tok = regexp(l{1}, '^(DataFile|MarkerFile)=(.*)$', 'tokens', 'once');
    if ~isempty(tok), s.(tok{1}) = strtrim(tok{2}); end
end
end

function rec = read_bdf(rec, path, hints)
hdr = ft_read_header(path, 'headerformat', 'biosemi_bdf');
dat = ft_read_data(path, 'header', hdr, 'dataformat', 'biosemi_bdf');
isstatus = strcmpi(hdr.label, 'Status');
rec.data = double(dat) .* to_uv(hdr);
rec.data(isstatus, :) = double(dat(isstatus, :));  % trigger values, not a voltage
rec.labels = hdr.label(:)';
rec.reader_types = repmat({'eeg'}, 1, numel(rec.labels));
rec.reader_types(isstatus) = {'stim'};
rec.srate = hdr.Fs;
rec.status_mask = 255;
if isfield(hints, 'bdf_status_mask'), rec.status_mask = hints.bdf_status_mask; end
rec.reader = 'fieldtrip ft_read_data (biosemi_bdf)';
rec.manufacturer = 'BioSemi';
rec.default_reference = 'CMS/DRL (reference-free)';
end

function f = to_uv(hdr)
% Per-channel factor to µV from the header units (FieldTrip keeps the file's units).
f = ones(numel(hdr.label), 1);
if ~isfield(hdr, 'chanunit'), return, end
for k = 1:numel(hdr.label)
    switch lower(strrep(hdr.chanunit{k}, char(181), 'u'))
        case 'v', f(k) = 1e6;
        case 'mv', f(k) = 1e3;
        case 'nv', f(k) = 1e-3;
    end
end
end
