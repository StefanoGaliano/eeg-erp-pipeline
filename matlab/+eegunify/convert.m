function [vhdr, rec, warnings] = convert(dumpPath, varargin)
%CONVERT  Convert one subject to canonical BIDS-EEG in MATLAB (Phase M ingestion).
%
%   eegunify convert's MATLAB twin. The config comes from the Python dump:
%     eegunify config dump code/configs/erpcore_n170.yaml --json --resolved -s 015 --out c.json
%   then
%     vhdr = eegunify.convert('c.json', 'root', projectRoot, 'bids_root', outDir)
%
%   Options: 'root' (project root; dataset.source_root is relative to it, default pwd),
%   'bids_root' (output folder, default <root>/<dataset.bids_root>).
%   Steps as eegunify.pipeline.read_recording: read -> stim channels kept by name ->
%   channel names -> types -> drops -> events (then stim channels removed) -> bads ->
%   units.declared -> validate -> write. Data stay in µV throughout (Python works in V and
%   writes µV; same numbers).
p = inputParser;
addParameter(p, 'root', pwd);
addParameter(p, 'bids_root', '');
parse(p, varargin{:});
root = char(p.Results.root);
cfg = eegunify.config_load(dumpPath);
c = cfg.config;
m = cfg.matlab;
bidsRoot = char(p.Results.bids_root);
if isempty(bidsRoot), bidsRoot = fullfile(root, c.dataset.bids_root); end
src = fullfile(root, c.dataset.source_root, strrep(c.recordings.pattern, '{subject}', cfg.subject));

r = eegunify.read_any(src, c.format.name, c.format.hints);
std = m.standard_1005;
% Stim channels keep their names so events can find them (Status, STI 014).
for k = 1:numel(r.labels)
    if strcmp(eegunify.infer_type(r.labels{k}, std), 'stim'), r.reader_types{k} = 'stim'; end
end
stim = r.labels(strcmp(r.reader_types, 'stim'));
names = eegunify.normalize_names(r.labels, m, stim);

% Types: configured > name rules; a misc channel gets a second try on its original name
% ("EOG L" lost its prefix); with no EEG at all, trust the reader's EEG typing.
confNames = {m.types.name};
types = cell(size(names));
for k = 1:numel(names)
    hit = find(strcmp(confNames, names{k}), 1);
    if ~isempty(hit)
        types{k} = m.types(hit).type;
        continue
    end
    if strcmp(r.reader_types{k}, 'stim')
        types{k} = 'stim';  % trigger channels stay triggers (an EGI track named "resp")
        continue
    end
    types{k} = eegunify.infer_type(names{k}, std, r.reader_types{k});
    if strcmp(types{k}, 'misc')
        types{k} = eegunify.infer_type(r.labels{k}, std, r.reader_types{k});
    end
end
if ~any(strcmp(types, 'eeg'))
    types(strcmp(types, 'misc') & strcmp(r.reader_types, 'eeg')) = {'eeg'};
end

drop = ismember(names, c.channels.drop) | ismember(r.labels, c.channels.drop);
r.data(drop, :) = []; names(drop) = []; types(drop) = []; r.labels(drop) = [];
r.reader_types(drop) = [];

r.reader_types = types;  % 'stim' where the channel is a trigger channel
[events, method] = eegunify.extract_events(r, cfg);
keep = ~strcmp(types, 'stim');

rec = struct();
rec.data = r.data(keep, :);
rec.names = names(keep);
rec.original = r.labels(keep);
rec.types = types(keep);
rec.srate = r.srate;
rec.source_srate = r.srate;
rec.events = events;
rec.event_method = method;
rec.bads = m.bads;
missing = setdiff(rec.bads, rec.names);
assert(isempty(missing), 'eegunify:bads', 'sub-%s: bad channels not in recording: %s', ...
    cfg.subject, strjoin(missing, ', '));
rec.unit_factor = 1;
if ~strcmp(c.units.declared, 'auto')
    toVolts = struct('V', 1, 'mV', 1e-3, 'uV', 1e-6);
    rec.unit_factor = toVolts.(c.units.declared);
    rec.data = rec.data * rec.unit_factor;
end
rec.reference = c.reference.online;
if strcmp(rec.reference, 'n/a') && ~isempty(r.default_reference)
    rec.reference = r.default_reference;
end
rec.sidecar = source_sidecar(src);
rec.manufacturer = r.manufacturer;
if isfield(rec.sidecar, 'Manufacturer'), rec.manufacturer = rec.sidecar.Manufacturer; end
rec.source_files = r.source_files;
rec.reader = r.reader;
rec.format = r.format;
rec.subject = cfg.subject;

warnings = eegunify.canonical_validate(rec, cfg);
for w = warnings
    fprintf('sub-%s [rule %d] %s\n', cfg.subject, w.rule, w.message);
end
vhdr = eegunify.write_bids(rec, cfg, bidsRoot);
end

function s = source_sidecar(src)
% The source's own BIDS _eeg.json (sub-01_task-x_eeg.set -> sub-01_task-x_eeg.json).
s = struct();
[folder, name] = fileparts(src);
path = fullfile(folder, [name '.json']);
if ~endsWith(name, '_eeg') || ~isfile(path), return, end
try
    s = jsondecode(fileread(path));
catch
    warning('eegunify:sidecar', 'could not parse source sidecar %s', path);
end
end
