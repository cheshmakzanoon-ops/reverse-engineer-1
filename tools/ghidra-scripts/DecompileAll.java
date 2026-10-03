//Decompile every function in the imported program to a single C-like file.
//
// Usage (headless):
//   analyzeHeadless <projDir> <projName> -import <binary> \
//     -scriptPath tools/ghidra-scripts \
//     -postScript DecompileAll.java <output.c> [nolibs]
//
// The optional "nolibs" argument skips thunks and functions with no body
// (imports / external symbols), which keeps output focused on real code.
//
//@category ReverseEngineering

import java.io.FileWriter;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.symbol.Reference;

public class DecompileAll extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("usage: DecompileAll <outputFile> [nolibs]");
            return;
        }

        String outputPath = args[0];
        boolean skipLibraries = args.length > 1 && args[1].equalsIgnoreCase("nolibs");

        DecompInterface decomp = new DecompInterface();
        DecompileOptions options = new DecompileOptions();
        decomp.setOptions(options);
        if (!decomp.openProgram(currentProgram)) {
            printerr("Failed to open program for decompilation.");
            return;
        }

        int written = 0;
        int skipped = 0;
        int failed = 0;

        try (PrintWriter out = new PrintWriter(new FileWriter(outputPath))) {
            out.println("/* Decompiled by Ghidra DecompileAll.java */");
            out.println("/* Program : " + currentProgram.getName() + " */");
            out.println("/* Language: " + currentProgram.getLanguageID() + " */");
            out.println();

            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext() && !monitor.isCancelled()) {
                Function function = functions.next();

                if (skipLibraries && (function.isThunk() || function.isExternal())) {
                    skipped++;
                    continue;
                }

                DecompileResults results = decomp.decompileFunction(function, 60, monitor);
                if (results == null || !results.decompileCompleted()
                        || results.getDecompiledFunction() == null) {
                    failed++;
                    out.println("/* ---- " + function.getName() + " @ "
                            + function.getEntryPoint() + " : decompilation failed ---- */");
                    out.println();
                    continue;
                }

                out.println("/* ---- " + function.getName() + " @ "
                        + function.getEntryPoint() + " ---- */");
                out.println(results.getDecompiledFunction().getC());

                // Inline the literal strings this function references; they are
                // usually the fastest way to identify a function's purpose.
                Reference[] refs = currentProgram.getReferenceManager()
                        .getReferencesFrom(function.getEntryPoint());
                for (Reference ref : refs) {
                    Data data = getDataAt(ref.getToAddress());
                    if (data != null && data.getValue() instanceof String) {
                        out.println("/* str-ref: " + data.getLabel()
                                + " = \"" + data.getValue() + "\" */");
                    }
                }

                out.println();
                written++;
            }
        } finally {
            decomp.dispose();
        }

        println("DecompileAll: wrote " + written + " functions to " + outputPath
                + " (skipped " + skipped + ", failed " + failed + ")");
    }
}