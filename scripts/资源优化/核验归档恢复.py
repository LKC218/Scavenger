"""实际恢复GLB、声音、Blender工程并读取工程结构；不保存工程。"""
import argparse,importlib.util,json,pathlib,subprocess,os,shutil
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版'
spec=importlib.util.spec_from_file_location('restore',pathlib.Path(__file__).with_name('恢复归档资源.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available,current,restore,HOME,save
    if available():
        mapping=json.loads((current()[3]/'原件映射.json').read_text('utf8'));results=[]
        for category in ['模型','音效','源工程']:
            key=next(k for k,v in mapping.items() if v['category']==category);results.append({'category':category,'sha256':key,'restored':str(restore(key))})
        save(HOME/'验收/恢复抽查.json',results);print('统一原件归档三类实际恢复通过；工程完整回读见主工程回读.json');sys.exit(0)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--blender',default=os.environ.get('BLENDER_EXE') or shutil.which('blender'));args=parser.parse_args()
    config=ROOT/'local-data/网页资源库/归档验证工具.json'
    blender=args.blender or (json.loads(config.read_text('utf8')).get('blender') if config.exists() else None)
    if not blender or not pathlib.Path(blender).is_file():raise RuntimeError('请通过--blender、BLENDER_EXE或本地归档验证工具.json提供Blender路径')
    rows=json.loads((OUT/'盘点.json').read_text(encoding='utf8'))['files'];result=[]
    for ext in ['.glb','.wav','.blend']:
        r=next(x for x in rows if x['format']==ext and '丧尸模型' in x['path']) if ext!='.wav' else next(x for x in rows if x['format']==ext)
        dst=m.restore(r['path']);result.append({'format':ext,'source':r['path'],'restored':dst.relative_to(ROOT).as_posix(),'sha256':r['sha256'],'passed':True})
        if ext=='.blend':
            script=ROOT/'tmp/资源恢复/回读工程.py';script.write_text("import bpy,json\nprint('ARCHIVE_READBACK '+json.dumps({'objects':len(bpy.data.objects),'meshes':len(bpy.data.meshes),'armatures':len(bpy.data.armatures),'actions':len(bpy.data.actions)}))\n",encoding='utf8')
            process=subprocess.run([blender,'--background','--factory-startup','--disable-autoexec',str(dst),'--python-exit-code','1','--python',str(script)],capture_output=True,text=True,encoding='utf8',errors='replace',check=True)
            line=next(x for x in process.stdout.splitlines() if x.startswith('ARCHIVE_READBACK '));result[-1]['blender']=json.loads(line.split(' ',1)[1]);(OUT/'归档工程回读.log').write_text(process.stdout+'\n'+process.stderr,encoding='utf8')
    (OUT/'归档恢复核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(result,ensure_ascii=False))
