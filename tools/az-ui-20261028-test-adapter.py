from pathlib import Path
import hashlib

path=Path('tests/verify-paid-upload-periods.cjs')
raw=path.read_bytes()
blob=lambda x:hashlib.sha1(b'blob '+str(len(x)).encode()+b'\0'+x).hexdigest()
assert blob(raw)=='851775d15ee67a152314cbc5ed3840f70f0d6f61'
s=raw.decode()
changes=[]
def change(old,new):
    global s
    assert s.count(old)==1,old[:80]
    s=s.replace(old,new)
    changes.append((old,new))
change("  const calls = [];\n  const timers = [];", "  form.querySelectorAll = selector => {\n    assert.equal(selector, 'input[type=\"file\"]');\n    return form.inputs.filter(input => input.type === 'file').map(input => ({files:[input.value]}));\n  };\n  const calls = [];\n  const timers = [];\n  const feedback = [];\n  let reloads = 0;")
change("    location: { reload() {}, replace() {} },", "    location: { reload() { reloads += 1; }, replace() {} },\n    window: {AZUploadFeedback: {show: options => feedback.push(options)}},")
change("  return { form, ids, calls, file, timers };", "  return { form, ids, calls, file, timers, feedback, reloads: () => reloads };")
change("  assert.equal(test.ids.paidClientResult.textContent, 'Сохранено.');\n  assert.equal(test.timers.length, 1);", "  assert.ok(test.ids.paidClientResult.textContent.startsWith('Сохранено.'));\n  assert.equal(test.timers.length, 0, 'No timed reload after a successful upload.');\n  assert.equal(test.feedback.length, 1);\n  assert.match(test.feedback[0].message, /selected-period.xlsx/);\n  assert.match(test.feedback[0].message, /01.01.2026.*30.06.2026/);\n  assert.equal(test.reloads(), 0);\n  test.feedback[0].onConfirm();\n  assert.equal(test.reloads(), 1, 'Reload only after explicit acknowledgement.');\n  assert.equal(test.calls.length, 2, 'Acknowledgement must never issue another import.');")
change("  assert.equal(test.ids.paidClientResult.textContent, 'Сохранены старые данные.');", "  assert.ok(test.ids.paidClientResult.textContent.startsWith('Сохранены старые данные.'));\n  assert.equal(test.feedback[0].status, 'duplicate');\n  assert.equal(test.reloads(), 0);")
restored=s
for old,new in reversed(changes):
    assert restored.count(new)==1
    restored=restored.replace(new,old)
assert blob(restored.encode())==blob(raw)
path.write_text(s)
workflow=Path('.github/workflows/az-upload-feedback-fold.yml')
s=workflow.read_text()
old="'tests/verify-paid-periods.cjs','tests/test_ui_feedback_fold.py'"
assert s.count(old)==1
workflow.write_text(s.replace(old,"'tests/verify-paid-periods.cjs','tests/verify-paid-upload-periods.cjs','tests/test_ui_feedback_fold.py'"))
print('RETAINED_UPLOAD_REQUEST_GUARDS_AND_EXPLICIT_OK_ASSERTIONS=PASS')
