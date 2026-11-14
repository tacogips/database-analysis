"""Supplemental host metadata; whitelist fields to exclude device identifiers."""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess
root=Path(__file__).resolve().parents[4]
def output(argv):return subprocess.check_output(argv,text=True).strip()
raw=json.loads(output(['system_profiler','SPHardwareDataType','-json']))['SPHardwareDataType'][0]
allowed=['machine_name','machine_model','chip_type','number_processors','physical_memory']
hardware={k:raw[k] for k in allowed if k in raw}
server=json.loads((root/'results/mysql-engine-count-100m/runs/20261108T153004Z-820d48697878bf5220e73128219ca4d3/result.json').read_text())
endpoint=server['context']['endpoint']
info=json.loads(output(['docker','--host',endpoint,'info','--format','{{json .}}']))
d={'capturedAt':datetime.now(timezone.utc).isoformat(),'scope':'Supplementary host metadata captured during existing run; does not modify its frozen runtime inputs. Hardware fields only; serial/UUID/UDID excluded.','hardware':hardware,'modelIdentifier':output(['sysctl','-n','hw.model']),'chip':output(['sysctl','-n','machdep.cpu.brand_string']),'memoryBytes':int(output(['sysctl','-n','hw.memsize'])),'physicalCpuCores':int(output(['sysctl','-n','hw.physicalcpu'])),'logicalCpuCores':int(output(['sysctl','-n','hw.logicalcpu'])),'macOS':{'product':output(['sw_vers','-productName']),'version':output(['sw_vers','-productVersion']),'build':output(['sw_vers','-buildVersion'])},'vm':{k:info[k] for k in ['Architecture','NCPU','MemTotal','ServerVersion','KernelVersion','OperatingSystem'] if k in info},'virtualization':'Colima VZ / virtiofs; Rosetta enabled for amd64 SQL Server, MySQL native arm64','mysqlContainerLimits':{'cpus':3,'memoryBytes':4*1024**3,'innodbBufferPoolBytes':1024**3,'myisamKeyBufferBytes':128*1024**2},'sqlServerContainerLimits':'Planned: CPU3, memory4GiB; actual runtime result must confirm.'}
for relative in ['results/reviews/mysql-engine-count-100m/host-spec.json','results/reviews/sqlserver-manual-count-100m/host-spec.json']:
 (root/relative).write_text(json.dumps(d,indent=2)+'\n')
print(json.dumps(d))
