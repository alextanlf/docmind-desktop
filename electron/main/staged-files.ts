import { constants } from 'node:fs'; import { access, lstat, mkdir, open, rename, unlink, copyFile } from 'node:fs/promises'; import { basename, extname, join } from 'node:path'; import { randomUUID } from 'node:crypto';
export class StagedFileError extends Error { constructor(public code: string, message: string){super(message)} }
export type StagedSource = { stagedSourceId: string; kind:'staged_file'; name:string; mediaType:string; sizeBytes:number }
export class StagedFileService { constructor(private opts:{dataDir:string; maxPdfBytes?:number; maxMarkdownBytes?:number}) {}
  async chooseAndStage(kind:'pdf'|'markdown') { return null }
  async stageSelected(source:string, kind:'pdf'|'markdown'): Promise<StagedSource> {
    const stat = await lstat(source).catch(()=>{throw new StagedFileError('SOURCE_UNSUPPORTED','Source unavailable')}); if (!stat.isFile()) throw new StagedFileError('SOURCE_UNSUPPORTED','Regular file required')
    const ext = extname(source).toLowerCase(); const allowed = kind==='pdf' ? ['.pdf'] : ['.md','.markdown']; if (!allowed.includes(ext)) throw new StagedFileError('SOURCE_UNSUPPORTED','Unsupported extension')
    const max = kind==='pdf' ? (this.opts.maxPdfBytes ?? 100*1024*1024) : (this.opts.maxMarkdownBytes ?? 20*1024*1024); if (stat.size > max) throw new StagedFileError('SOURCE_TOO_LARGE','Source exceeds size limit')
    const id = randomUUID(); const dir = join(this.opts.dataDir,'imports','staging'); await mkdir(dir,{recursive:true}); const partial = join(dir,id+ext+'.partial'); const final = join(dir,id+ext); try { await copyFile(source,partial,constants.COPYFILE_EXCL); const fh=await open(partial,'r+'); await fh.sync(); await fh.close(); await rename(partial,final) } catch (e) { await unlink(partial).catch(()=>{}); throw e }
    return { stagedSourceId:id, kind:'staged_file', name:basename(source), mediaType: kind==='pdf'?'application/pdf':'text/markdown', sizeBytes:stat.size }
  }
}
