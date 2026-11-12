// Unattended iBOM: read the PcbDoc path from ibom-job.txt, open it, call iBOM's own main(),
// write ok/fail + html path to ibom-result.txt for the ssh side to poll.
// Altium does not run top-level statements in this file, so constants live in a function.
function jobDir() {
    return "%USERPROFILE%\\codex-tools\\altium-autostart\\";
}

function writeResult(text) {
    var fso = new ActiveXObject("Scripting.FileSystemObject");
    var f = fso.CreateTextFile(jobDir() + "ibom-result.txt", true);
    f.WriteLine(text);
    f.Close();
}

function AutoIbom() {
    writeResult("started");  // tells "never called" apart from "stuck halfway"
    var fso = new ActiveXObject("Scripting.FileSystemObject");
    var jobFile = jobDir() + "ibom-job.txt";
    if (!fso.FileExists(jobFile)) { writeResult("fail no job file"); return; }
    var t = fso.OpenTextFile(jobFile, 1);
    var pcbPath = t.ReadLine();
    t.Close();
    try {
        var doc = Client.OpenDocument("PCB", pcbPath);
        if (doc == null) { writeResult("fail cannot open " + pcbPath); return; }
        Client.ShowDocument(doc);
        main();
        // same name rule as iBOM's own main(): text before the first dot
        var html = ExtractFilePath(pcbPath) + "PnPout\\" + ExtractFileName(pcbPath).split(".")[0] + ".html";
        writeResult((fso.FileExists(html) ? "ok " : "fail no html ") + html);
    } catch (e) {
        writeResult("fail " + e.message);
    } finally {
        // job consumed even on failure: a leftover job makes the next task run start iBOM instead of the bridge
        if (fso.FileExists(jobFile)) fso.DeleteFile(jobFile);
    }
}
