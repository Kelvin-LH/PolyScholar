# SPDX-License-Identifier: AGPL-3.0-only
"""Capture native screens using synthetic documents in an isolated temporary library."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QScrollArea
from PySide6.QtGui import QPdfWriter,QPainter
from polyscholar.app import Window,STYLE
from polyscholar.service import LocalService
from polyscholar.ui.scores import ScoreDetailDialog

def nav_to(window,name):
    """切换导航页;按名称查找,页面增减时不依赖行号。"""
    for row in range(window.nav.count()):
        if window.nav.item(row).text()==name:
            window.nav.setCurrentRow(row);return
    raise ValueError('导航页不存在:'+name)

def paint_pdf(path,line):
    writer=QPdfWriter(str(path));painter=QPainter(writer)
    painter.drawText(100,100,line);painter.end();del painter,writer

def paper_detail():
    """合成但结构真实的评分明细(与 aggregate 产物同构,不含任何私人数据)。"""
    dims=[dict(key='novelty',max=25,score=17,rationale='通过条目1-3;第4条缺近邻工作全文对照'),
          dict(key='innovation_degree',max=20,score=12,rationale='组合式改进,核心算子非新设计'),
          dict(key='effectiveness',max=40,score=27,rationale='公平基线但无意义阈值定义'),
          dict(key='rigor',max=10,score=8,rationale='方法闭合,威胁有控制'),
          dict(key='clarity',max=5,score=4,rationale='结论均可追踪')]
    return dict(rubric_version='1.0.0',manifest=dict(paper_sha256='synthetic'),total=68,dimensions=dims,
        strengths=[dict(point='多教师逐指标蒸馏',evidence='§3.1')],
        weaknesses=[dict(point='中心主张无结论表',evidence='§5')],
        sub_scores=[dict(agent_id='A',total=64),dict(agent_id='B',total=68),dict(agent_id='C',total=68)],
        aggregation=dict(method='median',spread=4,initial_spread=4,rechecked=False,rounds=0,
                         selected_agent_id='B',unresolved_disagreement=False))

def confidence_detail():
    dims=[dict(key='evidence',max=40,score=32,rationale='设置与口径可定位;无种子/方差报告'),
          dict(key='consistency',max=25,score=22,rationale='表图与正文数值交叉一致'),
          dict(key='traceability',max=15,score=14,rationale='引用与代码链接可定位'),
          dict(key='plausibility',max=20,score=20,rationale='声幅在文内约束内')]
    return dict(rubric_version='1.0.0',manifest=dict(paper_sha256='synthetic'),total=88,dimensions=dims,
        red_flags=[],verdict='高度可信',
        sub_scores=[dict(agent_id='A',total=87),dict(agent_id='B',total=88),dict(agent_id='C',total=88)],
        aggregation=dict(method='median',spread=1,initial_spread=1,rechecked=False,rounds=0,
                         selected_agent_id='B',unresolved_disagreement=False))

def summary_detail():
    def item(text,agents,evidence=None):
        entry=dict(text=text,agents=agents)
        if evidence:entry['evidence']=evidence
        return entry
    return dict(structure='summary-1',
        problem=[item('无高精地图下的闭环运动规划真值不可靠',['A','B'],'§1')],
        method=[item('多教师逐指标蒸馏',['A'],'§3.1'),item('时序BEV融合',['B'],'§3.2')],
        results=[item('nuPlan Val14 L2 较基线下降',['A','B'],'表2')],
        limitations=[item('无重复/方差报告',['A','B']),item('评测隔离程序未随材料提供',['A'],'§4')],
        meta=dict(agents=['A','B'],duplicates_dropped=1))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'design/screenshots')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    app=QApplication([]);app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-ui-') as tmp:
        root=Path(tmp);service=LocalService(data_dir=root/'data')
        parent=service.create_collection('课题资料');child=service.create_collection('相关方法',parent['id'])
        doc=None
        for i,title in enumerate(['Synthetic PDF · 本地阅读示例','Synthetic PDF · 方法整理']):
            path=root/f'sample-{i}.pdf';paint_pdf(path,f'Synthetic local UI test {i}')
            doc=service.import_pdf(path)
            service.update_document(doc['id'],{'title':title,'authors':'Demo Author','tags':['方法','待阅读']})
            service.set_membership(doc['id'],child['id'])
            supplement=root/f'supplement-{i}.pdf';paint_pdf(supplement,f'Synthetic supplement {i}')
            service.import_attachment(doc['id'],supplement,'supplement')
        # 三类评分全部写入,文献库与明细页展示真实渲染效果
        for kind,score,rationale,build in (('paper',68.0,'三盲评中位数68(极差4)。',paper_detail),
                                           ('confidence',88.0,'三盲评中位数88(极差1,高度可信)。',confidence_detail),
                                           ('summary',None,'双代理提炼:问题1/方法2/效果1/不足2条(合并1)。',summary_detail)):
            service.set_scores(doc['id'],[dict(kind=kind,score=score,rationale=rationale,
                                               detail=json.dumps(build(),ensure_ascii=False))])
        window=Window(service)
        try:
            window.show();app.processEvents();window.document_tree.setCurrentItem(window.document_tree.topLevelItem(0))
            app.processEvents();window.grab().save(str(args.output/'library-python.png'))
            dialog=ScoreDetailDialog(doc['title'],service.document_scores(doc['id']))
            dialog.resize(760,640);dialog.show();app.processEvents()
            dialog.grab().save(str(args.output/'score-detail-python.png'));dialog.close()
            window.attachment_list.setCurrentRow(1)
            attachment_scroll=window.attachment_list.parentWidget()
            while attachment_scroll and not isinstance(attachment_scroll,QScrollArea):attachment_scroll=attachment_scroll.parentWidget()
            if attachment_scroll:attachment_scroll.ensureWidgetVisible(window.attachment_delete_button)
            app.processEvents();window.grab().save(str(args.output/'attachments-python.png'))
            nav_to(window,'设置');app.processEvents();window.grab().save(str(args.output/'settings-python.png'))
            window.resize(1024,700);nav_to(window,'文献库');app.processEvents();window.grab().save(str(args.output/'library-1024.png'))
        finally:
            window.close();service.close()
    print('Native screenshots saved:',args.output)

if __name__=='__main__':main()
