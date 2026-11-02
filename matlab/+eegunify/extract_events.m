function [events, method] = extract_events(rec, cfg)
%EXTRACT_EVENTS  Event table from a stim channel or markers, as eegunify.events.extract.
%
%   [events, method] = eegunify.extract_events(rec, cfg)
%
%   events: table with onset (s), duration (s), sample (0-based), value (original code, text)
%   and trial_type. Codes map through events.mapping (integer, inclusive range "1-40", or a
%   label); events.unmapped decides about the rest; events.onset_shift_ms then moves onsets
%   by trial-type glob (first matching pattern wins) and recomputes sample.
%   Stim channel events follow MNE find_events(consecutive=False, initial_event=True): an
%   event wherever the masked value goes from 0 to non-zero.
ev = cfg.config.events;
m = cfg.matlab;
isstim = strcmp(rec.reader_types, 'stim');
source = ev.source;
if strcmp(source, 'auto')
    if any(isstim), source = 'stim_channel'; else, source = 'annotations'; end
end
switch source
    case 'stim_channel'
        ch = find(isstim, 1);
        if ~isempty(ev.stim_channel), ch = find(strcmp(rec.labels, ev.stim_channel), 1); end
        assert(~isempty(ch), 'eegunify:events', 'no stim channel found');
        raw = from_stim(rec.data(ch, :), rec.status_mask);
        method = ['stim_channel:' rec.labels{ch}];
        if isempty(raw.sample) && strcmp(ev.source, 'auto') && ~isempty(rec.markers)
            raw = from_markers(rec, cfg);
            method = 'annotations';
        end
    case 'annotations'
        raw = from_markers(rec, cfg);
        method = 'annotations';
    otherwise
        error('eegunify:events', 'events.source %s is not in the MATLAB package', source);
end
raw.onset = raw.sample / rec.srate;
if isfield(raw, 'onset_override'), raw.onset = raw.onset_override; end

ignore = ev.ignore_codes(:)';
keep = true(size(raw.sample));
tt = cell(size(raw.sample));
for k = 1:numel(raw.sample)
    v = raw.value{k};
    if isnumeric(v) && ismember(v, ignore)
        keep(k) = false;
        continue
    end
    t = lookup(m.event_mapping, v);
    if isempty(t)
        switch ev.unmapped
            case 'error'
                error('eegunify:events', 'event code %s has no mapping', val_str(v));
            case 'drop'
                keep(k) = false;
                continue
        end
        t = val_str(v);
    end
    tt{k} = t;
end
onset = raw.onset(keep);
duration = raw.duration(keep);
sample = raw.sample(keep);
value = cellfun(@val_str, raw.value(keep), 'UniformOutput', false);
tt = tt(keep);
if ~isempty(m.onset_shift_ms) && ~isempty(onset)
    shift = zeros(size(onset));
    for p = m.onset_shift_ms
        hit = ~cellfun(@isempty, regexp(tt, glob2regex(p.pattern), 'once')) & shift == 0;
        shift(hit) = p.ms / 1000;
    end
    onset = onset + shift;
    sample = round_half_even(onset * rec.srate);
end
[onset, order] = sort(onset);  % MATLAB sort is stable
col = @(x) reshape(x(order), [], 1);  % indexing a row with a column still gives a row
events = table(onset(:), col(duration), col(tt), col(value), col(sample), ...
    'VariableNames', {'onset', 'duration', 'trial_type', 'value', 'sample'});
end

function raw = from_stim(status, mask)
v = mod(round(status), 2^24);   % BDF Status is a signed 24-bit integer
v = bitand(v, mask);
prev = [0, v(1:end - 1)];
idx = find(v ~= 0 & prev == 0);  % includes sample 1 (initial_event) via prev = 0
raw.sample = idx - 1;
raw.duration = zeros(size(idx));
raw.value = num2cell(v(idx));
end

function raw = from_markers(rec, cfg)
housekeeping = {'new segment/', 'boundary', 'edf annotations', 'bad', 'bad_acq_skip', ...
    'impedance', 'marker/impedance', 'impedance/'};
mk = rec.markers;
keep = true(size(mk));
for k = 1:numel(mk)
    d = mk(k).description;
    keep(k) = ~(ismember(lower(strtrim(d)), housekeeping) || startsWith(upper(d), 'BAD'));
end
mk = mk(keep);
onset = [mk.sample] / rec.srate;
raw.onset_override = onset;               % markers keep their exact onset
raw.sample = round_half_even(onset * rec.srate);
raw.duration = [mk.duration];
raw.value = cell(1, numel(mk));
for k = 1:numel(mk)
    raw.value{k} = parse_code(mk(k).description, cfg.config.format.hints.bdf_status_mask);
end
if isempty(mk)
    raw.sample = zeros(1, 0); raw.duration = zeros(1, 0); raw.onset_override = zeros(1, 0);
end
end

function c = parse_code(d, statusMask)
% Python parse_code, plus BioSemi STATUS/<n> markers masked like the Status channel.
d = strtrim(d);
tok = regexpi(d, '^STATUS/(\d+)$', 'tokens', 'once');
if ~isempty(tok), c = bitand(str2double(tok{1}), statusMask); return, end
if ~isempty(regexp(d, '^-?\d+$', 'once')), c = str2double(d); return, end
tok = regexp(d, '^(?:Stimulus|Response)/[SR]\s*(\d+)$', 'tokens', 'once');
if ~isempty(tok), c = str2double(tok{1}); return, end
tok = regexpi(d, '^condition\s+(\d+)$', 'tokens', 'once');
if ~isempty(tok), c = str2double(tok{1}); return, end
c = d;
end

function t = lookup(mapping, v)
t = '';
if ischar(v)
    collapse = @(s) regexprep(strtrim(s), '\s+', ' ');
    for p = mapping
        if isempty(regexp(p.key, '^\s*\d+(\s*-\s*\d+)?\s*$', 'once')) && strcmp(collapse(p.key), collapse(v))
            t = p.trial_type;
            return
        end
    end
    return
end
for p = mapping
    tok = regexp(p.key, '^\s*(\d+)\s*-\s*(\d+)\s*$', 'tokens', 'once');
    if isempty(tok)
        tok = regexp(p.key, '^\s*(\d+)\s*$', 'tokens', 'once');
        if isempty(tok), continue, end
        tok = [tok tok]; %#ok<AGROW>
    end
    if v >= str2double(tok{1}) && v <= str2double(tok{2})
        t = p.trial_type;
        return
    end
end
end

function s = val_str(v)
if isnumeric(v), s = sprintf('%d', v); else, s = v; end
end

function re = glob2regex(g)
% fnmatch: * any run, ? one character, everything else literal.
re = ['^' regexprep(regexptranslate('escape', g), {'\\\*', '\\\?'}, {'.*', '.'}) '$'];
end

function r = round_half_even(x)
% numpy/Python rounding (ties to even); MATLAB round sends ties away from zero.
r = round(x);
tie = abs(x - fix(x)) == 0.5;
r(tie) = 2 * round(x(tie) / 2);
end
