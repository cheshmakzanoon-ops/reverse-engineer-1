//Name and decompile IL2CPP game code in a GameAssembly-style Mach-O.
//
// An IL2CPP binary compiles every managed method -- game code, Unity, and the
// whole BCL -- into one flat __TEXT.  Warped Kart Racers alone has 194,548
// managed methods, and decompiling all of them needs far more RAM than a
// small CI box has.  But only a fraction is ours: Unity's 31k methods and the
// BCL's 120k are already-known code.
//
// So this script reads an Il2CppDumper `script.json`, and (unless told
// otherwise) touches ONLY methods belonging to the game's own assemblies.
// It applies Il2CppDumper's real names first, so the decompiled output reads
// like the original C# rather than `FUN_0010abc00`, then writes them out
// grouped by type.
//
// Input JSON is Il2CppDumper's format:
//   { "ScriptMethod": [ { "Address": <int>, "Name": "NS.Type$$Method",
//                         "Signature": "...", "TypeSignature": "v" }, ... ] }
//
// Functions are matched to JSON entries by ADDRESS, not by name: Il2CppDumper
// names collide after sanitising, and Ghidra appends disambiguating suffixes,
// so a name lookup is both slower and less reliable than the address.
//
// Usage (headless):
//   analyzeHeadless <projDir> <projName> -process <binary> -noanalysis \
//     -scriptPath tools/ghidra-scripts \
//     -postScript DecompileIl2Cpp.java <script.json> <output.c> [all] [nolibs]
//
//   <script.json>  Il2CppDumper script.json (or a pre-filtered subset).
//   <output.c>     where to write the decompiled C.
//   all            also decompile non-game assemblies (slow, RAM hungry).
//   nolibs         skip thunks and external functions.
//
// Assemblies considered "game" (when `all` is absent): anything whose type
// name starts with Atlas, Core, Assembly-CSharp, UI2, StateManagement or
// ElectricSquare.
//
//@category ReverseEngineering

import java.io.FileReader;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.symbol.SourceType;

public class DecompileIl2Cpp extends GhidraScript {

    private static final String[] GAME_PREFIXES = {
        "Atlas", "Core", "Assembly-CSharp", "UI2", "StateManagement", "ElectricSquare"
    };

    /** Turn an Il2CppDumper method name into a valid Ghidra identifier. */
    private static String sanitize(String name) {
        StringBuilder sb = new StringBuilder(name.length());
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            if (Character.isLetterOrDigit(c) || c == '_' || c == '.') {
                sb.append(c);
            }
            else if (c == '$') {
                sb.append('.');
            }
            else if (c == '<') {
                sb.append('_');          // generic arity: List`1<Foo> -> List_1_Foo
            }
            else {
                sb.append('_');          // '>', ' ', '-', '+', ',', '-'
            }
        }
        return sb.toString();
    }

    /** "NS.Type$$Method" -> "NS.Type.Method". */
    private static String qualified(String raw) {
        return sanitize(raw.replace("$$", ".").replace("::", "."));
    }

    private static boolean isGameAssembly(String qualifiedName) {
        for (String p : GAME_PREFIXES) {
            if (qualifiedName.equals(p) || qualifiedName.startsWith(p + ".")
                    || qualifiedName.startsWith(p + "_")) {
                return true;
            }
        }
        return false;
    }

    /** Pull `"Key": value` out of a JSON object substring, string-aware. */
    private static String field(String obj, String key) {
        String needle = "\"" + key + "\"";
        int i = obj.indexOf(needle);
        if (i < 0) {
            return null;
        }
        int colon = obj.indexOf(':', i + needle.length());
        if (colon < 0) {
            return null;
        }
        int p = colon + 1;
        while (p < obj.length() && Character.isWhitespace(obj.charAt(p))) {
            p++;
        }
        if (p >= obj.length()) {
            return null;
        }
        if (obj.charAt(p) == '"') {
            StringBuilder sb = new StringBuilder();
            for (p++; p < obj.length(); p++) {
                char c = obj.charAt(p);
                if (c == '\\') {
                    p++;
                    continue;
                }
                if (c == '"') {
                    break;
                }
                sb.append(c);
            }
            return sb.toString();
        }
        int end = p;
        while (end < obj.length() && "-+.eE0123456789".indexOf(obj.charAt(end)) >= 0) {
            end++;
        }
        return obj.substring(p, end);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: DecompileIl2Cpp <script.json> <output.c> [all] [nolibs]");
            return;
        }
        String jsonPath = args[0];
        String outPath = args[1];
        boolean allAssemblies = false;
        boolean skipLibraries = false;
        for (int i = 2; i < args.length; i++) {
            if (args[i].equalsIgnoreCase("all")) {
                allAssemblies = true;
            }
            else if (args[i].equalsIgnoreCase("nolibs")) {
                skipLibraries = true;
            }
        }

        // ---- scan script.json for the methods we care about ------------
        // The file is tens of MB; a generic DOM would cost more heap than the
        // decompiler gets.  We only need Address and Name, so scan for
        // `"Address"` occurrences and pull the fields out of each object.
        List<String[]> wanted = new ArrayList<>();   // {rva, qualifiedName}
        long seenEntries = 0;
        int kept = 0;

        try (FileReader fr = new FileReader(jsonPath)) {
            StringBuilder chunk = new StringBuilder();
            char[] buf = new char[1 << 16];
            int n;
            while ((n = fr.read(buf)) > 0) {
                chunk.append(buf, 0, n);
                int from = 0;
                while (true) {
                    int start = chunk.indexOf("\"Address\"", from);
                    if (start < 0) {
                        break;
                    }
                    int end = chunk.indexOf("}", start);
                    if (end < 0) {
                        break;
                    }
                    String obj = chunk.substring(start, end);
                    String addr = field(obj, "Address");
                    String name = field(obj, "Name");
                    if (addr != null && name != null) {
                        seenEntries++;
                        String q = qualified(name);
                        if (allAssemblies || isGameAssembly(q)) {
                            wanted.add(new String[] { addr, q });
                            kept++;
                        }
                    }
                    from = end + 1;
                }
                if (from > 0) {
                    chunk.delete(0, from);
                }
                if (chunk.length() > (1 << 20)) {
                    chunk.setLength(0);
                }
                if (monitor.isCancelled()) {
                    break;
                }
            }
        }
        println("DecompileIl2Cpp: " + seenEntries + " entries in " + jsonPath
                + "; " + kept + " selected");

        // ---- index existing functions by RVA ----------------------------
        Address base = currentProgram.getImageBase();
        Map<Long, Function> byRva = new HashMap<>();
        FunctionIterator fit = currentProgram.getFunctionManager().getFunctions(true);
        int totalFunctions = 0;
        while (fit.hasNext()) {
            Function f = fit.next();
            byRva.put(f.getEntryPoint().subtract(base), f);
            totalFunctions++;
        }
        println("DecompileIl2Cpp: program has " + totalFunctions + " defined functions");

        // ---- create + name the functions Il2CppDumper knows about -------
        int created = 0, renamed = 0;
        for (String[] w : wanted) {
            if (monitor.isCancelled()) {
                break;
            }
            long rva;
            try {
                rva = Long.parseLong(w[0]);
            }
            catch (NumberFormatException e) {
                continue;
            }
            Address addr = base.add(rva);
            Function f = byRva.get(rva);
            if (f == null) {
                f = createFunction(addr, null);
                if (f != null) {
                    byRva.put(rva, f);
                    created++;
                }
            }
            if (f != null && !w[1].equals(f.getName())) {
                try {
                    f.setName(w[1], SourceType.USER_DEFINED);
                    renamed++;
                }
                catch (Exception e) {
                    // name collision or invalid identifier -- harmless
                }
            }
        }
        println("DecompileIl2Cpp: created " + created + ", renamed " + renamed);

        // ---- decompile, grouped by owning type -------------------------
        DecompInterface decomp = new DecompInterface();
        decomp.setOptions(new DecompileOptions());
        if (!decomp.openProgram(currentProgram)) {
            printerr("Failed to open program for decompilation.");
            return;
        }

        // owning type -> ordered "rva\0qualifiedName" strings, so the output
        // groups naturally by class instead of by address
        Map<String, List<String>> byType = new TreeMap<>();
        for (String[] w : wanted) {
            String q = w[1];
            int lastDot = q.lastIndexOf('.');
            String type = lastDot > 0 ? q.substring(0, lastDot) : "(global)";
            byType.computeIfAbsent(type, k -> new ArrayList<>()).add(w[0] + " " + q);
        }

        int written = 0, failed = 0, missing = 0, skipped = 0;
        try (PrintWriter out = new PrintWriter(outPath)) {
            out.println("/* Decompiled by Ghidra DecompileIl2Cpp.java */");
            out.println("/* Program : " + currentProgram.getName() + " */");
            out.println("/* Language: " + currentProgram.getLanguageID() + " */");
            out.println("/* Scope   : " + (allAssemblies ? "ALL assemblies" : "game assemblies only")
                    + " (" + kept + " methods selected) */");
            out.println();

            for (Map.Entry<String, List<String>> e : byType.entrySet()) {
                out.println("/* ===================== " + e.getKey() + " ===================== */");
                for (String item : e.getValue()) {
                    if (monitor.isCancelled()) {
                        break;
                    }
                    int sep = item.indexOf(' ');
                    long rva;
                    try {
                        rva = Long.parseLong(item.substring(0, sep));
                    }
                    catch (NumberFormatException ex) {
                        continue;
                    }
                    String qual = item.substring(sep + 1);

                    Function f = byRva.get(rva);
                    if (f == null) {
                        missing++;
                        continue;
                    }
                    if (skipLibraries && (f.isThunk() || f.isExternal())) {
                        skipped++;
                        continue;
                    }
                    DecompileResults r = decomp.decompileFunction(f, 90, monitor);
                    if (r == null || !r.decompileCompleted()
                            || r.getDecompiledFunction() == null) {
                        failed++;
                        continue;
                    }
                    out.println("/* ---- " + qual + " @ " + f.getEntryPoint()
                            + "  (RVA 0x" + Long.toHexString(rva) + ") ---- */");
                    out.println(r.getDecompiledFunction().getC());
                    out.println();
                    written++;
                }
            }
        }
        finally {
            decomp.dispose();
        }

        println("DecompileIl2Cpp: wrote " + written + " methods to " + outPath
                + " (no function: " + missing + ", skipped " + skipped
                + ", failed " + failed + ")");
    }
}