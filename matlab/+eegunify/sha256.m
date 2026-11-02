function h = sha256(path)
%SHA256  Hex sha256 of a file (Java MessageDigest; MATLAB has no built-in file hash).
md = java.security.MessageDigest.getInstance('SHA-256');
fid = fopen(path, 'r');
assert(fid > 0, 'eegunify:sha256', 'cannot open %s', path);
closer = onCleanup(@() fclose(fid));
while true
    block = fread(fid, 2^20, '*uint8');
    if isempty(block), break, end
    md.update(typecast(block, 'int8'));
end
h = lower(reshape(dec2hex(typecast(md.digest(), 'uint8'), 2)', 1, []));
end
