function cfg = config_load(jsonPath)
%CONFIG_LOAD  Read a config dumped by `eegunify config dump <yaml> --json --resolved -s <sub>`.
%
%   cfg = eegunify.config_load('dump.json')
%
%   The Python side resolves subject overrides and the component library, so MATLAB never
%   re-implements merge logic (docs/BLUEPRINT_PHASES.md section 11). cfg.config is the full
%   config, cfg.matlab holds mappings as lists of pairs (jsondecode would turn a key such as
%   "1-40" into a field name) and the channel-name tables, cfg.config_hash goes into the
%   provenance of everything MATLAB writes.
assert(isfile(jsonPath), 'eegunify:config', 'config dump %s not found', jsonPath);
cfg = jsondecode(fileread(jsonPath));
assert(isfield(cfg, 'dump_version') && cfg.dump_version == 1, 'eegunify:config', ...
    '%s is not an eegunify config dump (version 1)', jsonPath);
assert(~isempty(cfg.subject), 'eegunify:config', ...
    'dump the config for one subject (eegunify config dump ... -s <subject>)');
m = cfg.matlab;
% Lists of pairs come back as struct arrays, or [] when empty: make them struct arrays always.
m.event_mapping = pairs(m.event_mapping, {'key', 'trial_type'});
m.onset_shift_ms = pairs(m.onset_shift_ms, {'pattern', 'ms'});
m.rename = pairs(m.rename, {'from', 'to'});
m.types = pairs(m.types, {'name', 'type'});
m.aliases = pairs(m.aliases, {'from', 'to'});
m.bads = to_cellstr(m.bads);
m.standard_1005 = cellstr(m.standard_1005(:)');
m.biosemi64 = cellstr(m.biosemi64(:)');
m.inherited_sidecar_keys = cellstr(m.inherited_sidecar_keys(:)');
cfg.matlab = m;
% jsondecode turns [] into a double; string lists used with ismember must be cellstr.
cfg.config.channels.drop = to_cellstr(cfg.config.channels.drop);
end

function c = to_cellstr(x)
if isempty(x), c = {}; else, c = cellstr(x(:)'); end
end

function s = pairs(s, fields)
if isempty(s)
    s = cell2struct(cell(numel(fields), 0), fields, 1);
end
s = s(:)';
end
