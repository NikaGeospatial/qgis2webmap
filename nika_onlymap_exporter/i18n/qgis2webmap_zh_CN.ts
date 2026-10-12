<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="zh_CN">
  <context>
    <name>qgis2webmap</name>
    <message>
      <location filename="../plugin.py" line="37" />
      <source>Create web map</source>
      <translation>创建网页地图</translation>
    </message>
    <message>
      <location filename="../plugin.py" line="128" />
      <source>Export the current QGIS project to a portable OnlyMap web map</source>
      <translation>将当前 QGIS 项目导出为可移植的 OnlyMap 网页地图</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="163" />
      <source>Open Web -&gt; QGIS2WebMap by NIKA once and run an export from the dialog. It shows the OnlyMap licence and installs the runtime, after which this algorithm works offline. On a machine with no internet access, install the runtime pack and set ONLYMAP_RUNTIME_DIR.</source>
      <translation>请打开“网络 -&gt; QGIS2WebMap by NIKA”一次，并在对话框中执行一次导出。它会显示 OnlyMap 许可证并安装运行时，之后此算法即可离线运行。在无法联网的电脑上，请安装运行时包并设置 ONLYMAP_RUNTIME_DIR。</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="46" />
      <source>Standalone HTML (one file)</source>
      <translation>独立 HTML（单个文件）</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="47" />
      <source>Share ZIP</source>
      <translation>分享用 ZIP</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="48" />
      <source>Folder</source>
      <translation>文件夹</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="72" />
      <source>Export to OnlyMap web map</source>
      <translation>导出为 OnlyMap 网络地图</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="75" />
      <source>Web map</source>
      <translation>网络地图</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="81" />
      <source>Exports the current QGIS project to a portable OnlyMap web map.

The default output is a single HTML file another person can double-click and use without QGIS. Exported maps send one anonymous usage report to NIKA when they load, and nothing else — see docs/privacy.md.

Anything that cannot be translated exactly is reported in the log rather than dropped silently. If part of the project cannot be read at all (a Blocked item, such as a layer whose data source is missing), the export stops and lists the problems, unless 'Export even if some items are blocked' is ticked - then the map is written without them and each one is logged as a warning. For the full fidelity report, and for per-layer popup and label choices, use Web → QGIS2WebMap by NIKA.

&lt;b&gt;First run on a new computer:&lt;/b&gt; open Web → QGIS2WebMap by NIKA and export once from there. It shows the OnlyMap runtime licence and installs the runtime ({size}, once per computer). This algorithm cannot do that itself - there is no way to show a licence during a batch run - so until then it stops immediately and says so. Every run after that works offline.</source>
      <translation>将当前 QGIS 项目导出为可移植的 OnlyMap 网络地图。

默认输出是一个 HTML 文件，其他人双击即可使用，无需 QGIS。导出的地图在加载时会向 NIKA 发送一次匿名使用报告，除此之外不发送任何内容——见 docs/privacy.md。

无法精确转换的内容会记录在日志中，而不会被悄悄丢弃。如果项目的某部分完全无法读取（被阻止的项，例如数据源缺失的图层），导出会停止并列出问题，除非勾选了“即使部分项被阻止也导出”——此时地图会在缺少这些项的情况下写出，并将每一项记录为警告。如需完整的保真度报告以及每个图层的弹窗和标注选项，请使用“网络 → QGIS2WebMap by NIKA”。

&lt;b&gt;在新电脑上首次运行：&lt;/b&gt;打开“网络 → QGIS2WebMap by NIKA”并从那里导出一次。它会显示 OnlyMap 运行时许可证并安装运行时（{size}，每台电脑一次）。此算法无法自行完成这一步——批处理运行中无法显示许可证——因此在此之前它会立即停止并说明原因。之后的每次运行都可离线进行。</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="109" />
      <source>Output mode</source>
      <translation>输出类型</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="117" />
      <source>Map title (blank uses the project title)</source>
      <translation>地图标题（留空则使用项目标题）</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="128" />
      <source>OnlyMap licence key (blank uses ONLYMAP_LICENSE_KEY, then any key already saved on this computer; only needed for a map you publish to a web server)</source>
      <translation>OnlyMap 许可证密钥（留空则使用 ONLYMAP_LICENSE_KEY，再使用此电脑上已保存的密钥；仅发布到网络服务器的地图需要）</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="137" />
      <location filename="../ui/main_dialog.py" line="1859" />
      <source>Legend</source>
      <translation>图例</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="138" />
      <location filename="../ui/main_dialog.py" line="1860" />
      <source>Layer switcher</source>
      <translation>图层切换器</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="139" />
      <location filename="../ui/main_dialog.py" line="1861" />
      <source>Zoom controls</source>
      <translation>缩放控件</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="140" />
      <location filename="../ui/main_dialog.py" line="1862" />
      <source>Scale bar</source>
      <translation>比例尺</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="150" />
      <source>Export even if some items are blocked</source>
      <translation>即使部分项被阻止也导出</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="158" />
      <source>Exported map</source>
      <translation>导出的地图</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="246" />
      <source>There is nothing to export. Add at least one vector or raster layer with data to the project.</source>
      <translation>没有可导出的内容。请向项目添加至少一个含数据的矢量或栅格图层。</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="272" />
      <source>Exporting anyway, as requested: {n} blocked item will be missing from the map.</source>
      <translation>已按要求仍然导出：地图中将缺少 {n} 个被阻止的项。</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="272" />
      <source>Exporting anyway, as requested: {n} blocked items will be missing from the map.</source>
      <translation>已按要求仍然导出：地图中将缺少 {n} 个被阻止的项。</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="159" />
      <source>HTML files (*.html);;ZIP files (*.zip)</source>
      <translation>HTML 文件 (*.html);;ZIP 文件 (*.zip)</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="237" />
      <source>'{path}' is an existing file, and folder mode needs a directory. Choose a path that is a folder or does not exist yet.</source>
      <translation>“{path}”是已存在的文件，而“文件夹”类型需要一个目录。请选择一个文件夹路径，或尚不存在的路径。</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="266" />
      <source>Tick 'Export even if some items are blocked' to export the rest anyway.</source>
      <translation>勾选“即使部分项被阻止也导出”即可仍然导出其余部分。</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="282" />
      <source>Missing from the map - {subject}: {detail}</source>
      <translation>地图中缺少 - {subject}：{detail}</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="298" />
      <source>The export was blocked so it could not write a broken map:</source>
      <translation>导出已被阻止，以免写出损坏的地图：</translation>
    </message>
    <message>
      <location filename="../processing/export_project.py" line="259" />
      <source>Some of the project could not be read, so the map would be missing it:</source>
      <translation>项目的部分内容无法读取，因此地图中会缺少它们：</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="47" />
      <source>Sign in to NIKA</source>
      <translation>登录 NIKA</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="308" />
      <source>Click to switch account or sign out.</source>
      <translation>点击以切换账户或退出登录。</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="155" />
      <source>Pair this QGIS profile with your NIKA account so you can publish maps to it. Your browser opens NIKA's sign-in page; the plugin never sees your password.</source>
      <translation>将此 QGIS 配置文件与你的 NIKA 账户关联，以便向其发布地图。浏览器会打开 NIKA 登录页面；插件不会看到你的密码。</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="213" />
      <source>Switch account…</source>
      <translation>切换账户…</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="216" />
      <source>Sign in as somebody else. Your browser opens NIKA's sign-in page; this account stays paired until the new one is approved.</source>
      <translation>使用其他账户登录。浏览器会打开 NIKA 登录页面；在新账户获批之前，此账户保持关联。</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="223" />
      <source>Sign out</source>
      <translation>退出登录</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="226" />
      <source>Unpair this QGIS profile and revoke its access to your NIKA account. Maps you have already published stay online.</source>
      <translation>解除此 QGIS 配置文件的关联，并撤销其对你 NIKA 账户的访问权限。已发布的地图仍保持在线。</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="273" />
      <source>Sign out of NIKA</source>
      <translation>退出 NIKA</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="291" />
      <source>Not in this version yet</source>
      <translation>此版本尚未提供</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="292" />
      <source>Signing in to NIKA arrives with map hosting in a later release. Everything this plugin exports today is a file you own and can share yourself - nothing here needs an account.</source>
      <translation>NIKA 登录将在后续版本中随地图托管一同推出。目前此插件导出的一切都是归你所有、由你自行分享的文件——这里的任何功能都不需要账户。</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="302" />
      <source>Signed in to NIKA as {account}.</source>
      <translation>已以 {account} 身份登录 NIKA。</translation>
    </message>
    <message>
      <location filename="../ui/account_button.py" line="274" />
      <source>Sign out of {account} and revoke this computer's access?

Maps you have already published stay online. You will need to sign in again through your browser to publish anything new.</source>
      <translation>要退出 {account} 并撤销这台电脑的访问权限吗？

已发布的地图仍会保持在线。若要发布新内容，需要再次通过浏览器登录。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="119" />
      <source>✓ exact</source>
      <translation>✓ 完全一致</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="120" />
      <source>Select a row to see where it applies and where to change it.</source>
      <translation>选择一行，查看它的适用范围以及在哪里修改。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="121" />
      <source>Tick more of the boxes above, or clear the search.</source>
      <translation>请勾选上方更多选项，或清除搜索。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="122" />
      <source>As above.</source>
      <translation>同上。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="136" />
      <source>Cannot be carried into the map at all.</source>
      <translation>完全无法带入地图。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="137" />
      <source>Cannot be represented, and was left out.</source>
      <translation>无法表示，已省略。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="138" />
      <source>Exported, but approximated - the detail says how.</source>
      <translation>已导出，但为近似效果——详情中有说明。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="141" />
      <source>Drawn into the map as an image.</source>
      <translation>以图像形式绘制到地图中。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="142" />
      <source>Survives exactly as set in QGIS. Hidden by default so the changes stand out; nothing is left out of the report.</source>
      <translation>与 QGIS 中的设置完全一致。默认隐藏，以便突出变化；报告不会遗漏任何内容。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="155" />
      <source>Kept</source>
      <translation>保留</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="156" />
      <source>Changed</source>
      <translation>已更改</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="157" />
      <source>Rasterised</source>
      <translation>已栅格化</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="158" />
      <source>Not exported</source>
      <translation>未导出</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="159" />
      <source>Blocked</source>
      <translation>已阻止</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="160" />
      <source>Symbology</source>
      <translation>符号系统</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="161" />
      <location filename="../ui/main_dialog.py" line="1508" />
      <source>Labels</source>
      <translation>标注</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="162" />
      <source>Popups &amp; fields</source>
      <translation>弹窗和字段</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="163" />
      <location filename="../ui/main_dialog.py" line="948" />
      <source>Data</source>
      <translation>数据</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="164" />
      <source>Map settings</source>
      <translation>地图设置</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="165" />
      <source>Other</source>
      <translation>其他</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="210" />
      <source>Show in Layers</source>
      <translation>在“图层”中显示</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="859" />
      <source>Export anyway</source>
      <translation>仍然导出</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="860" />
      <source>You can export anyway and the map is made without it, or review the report first.</source>
      <translation>你可以仍然导出，地图将不包含这一项；也可以先查看报告。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="864" />
      <source>You can export anyway and the map is made without them, or review the report first.</source>
      <translation>你可以仍然导出，地图将不包含这些项；也可以先查看报告。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="882" />
      <source>Part of this map will not work</source>
      <translation>此地图的部分内容将无法正常工作</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="897" />
      <source>Review on the Fidelity tab</source>
      <translation>在“保真度”标签页中查看</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="211" />
      <source>Go to {tab} tab</source>
      <translation>前往“{tab}”标签页</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="237" />
      <source>What survives the export, and what does not - checked before you export, so nothing is left to discover later.</source>
      <translation>导出后哪些内容会保留、哪些不会——在导出前就检查好，事后不会有意外。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="251" />
      <location filename="../ui/fidelity_panel.py" line="442" />
      <source>Not checked yet.</source>
      <translation>尚未检查。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="257" />
      <location filename="../ui/fidelity_panel.py" line="443" />
      <source>Check now</source>
      <translation>立即检查</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="286" />
      <source>Out of date - something changed since this check, so the rows below describe the map as it was.</source>
      <translation>已过期——此次检查后有内容发生变化，下方各行描述的是之前的地图。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="343" />
      <location filename="../ui/fidelity_panel.py" line="562" />
      <location filename="../ui/fidelity_panel.py" line="582" />
      <source>Filter</source>
      <translation>筛选</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="352" />
      <source>Show</source>
      <translation>显示</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="372" />
      <location filename="../ui/main_dialog.py" line="2545" />
      <source>About</source>
      <translation>关于</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="384" />
      <source>Search the report</source>
      <translation>搜索报告</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="449" />
      <source>Not checked yet</source>
      <translation>尚未检查</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="449" />
      <source>Opening this tab reads the project to check.</source>
      <translation>打开此标签页会读取项目进行检查。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="456" />
      <source>Checking what the export changes...</source>
      <translation>正在检查导出会改变什么...</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="469" />
      <source>The check could not finish.</source>
      <translation>检查未能完成。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="470" />
      <source>Try again</source>
      <translation>重试</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="475" />
      <source>Check failed</source>
      <translation>检查失败</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="475" />
      <source>See the detail for why.</source>
      <translation>原因请见详情。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="477" />
      <source>The check could not finish</source>
      <translation>检查未能完成</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="302" />
      <source>What</source>
      <translation>内容</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="302" />
      <source>Result</source>
      <translation>结果</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="302" />
      <source>Detail</source>
      <translation>详情</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="461" />
      <source>Checking...</source>
      <translation>正在检查...</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="462" />
      <source>Reading the project to see what the export changes.</source>
      <translation>正在读取项目，查看导出会改变什么。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="485" />
      <source>Not a verdict on the map</source>
      <translation>并非对地图的结论</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="510" />
      <source>Check again</source>
      <translation>重新检查</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="636" />
      <source>Nothing matches these filters</source>
      <translation>没有符合这些筛选条件的内容</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="637" />
      <source>The report has rows the filters above hide. Tick more of them, or clear the search.</source>
      <translation>报告中有被上方筛选条件隐藏的行。请勾选更多选项，或清除搜索。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="711" />
      <source>Nothing about this layer changes on the way out.</source>
      <translation>此图层导出后没有任何变化。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="885" />
      <source>{n} thing cannot be carried into the map as set up in QGIS:</source>
      <translation>有 {n} 项无法按 QGIS 中的设置带入地图：</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="885" />
      <source>{n} things cannot be carried into the map as set up in QGIS:</source>
      <translation>有 {n} 项无法按 QGIS 中的设置带入地图：</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="479" />
      <source>The project could not be read: {message}

The full error is in the QGIS message log. Nothing about the map is known until a check finishes, so none of it is shown as a verdict.</source>
      <translation>无法读取项目：{message}

完整错误信息在 QGIS 消息日志中。检查完成前对地图一无所知，因此不会显示任何结论。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="577" />
      <source>Filter - {n} row hidden</source>
      <translation>筛选 - 已隐藏 {n} 行</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="577" />
      <source>Filter - {n} rows hidden</source>
      <translation>筛选 - 已隐藏 {n} 行</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="579" />
      <source>Filter - some rows hidden</source>
      <translation>筛选 - 部分行已隐藏</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="499" />
      <source>Map: {facts}</source>
      <translation>地图：{facts}</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="645" />
      <source>Nothing matches these filters.</source>
      <translation>没有符合这些筛选条件的内容。</translation>
    </message>
    <message>
      <location filename="../ui/fidelity_panel.py" line="731" />
      <source>Applies to: {subjects}.</source>
      <translation>适用于：{subjects}。</translation>
    </message>
    <message>
      <location filename="../ui/language_picker.py" line="83" />
      <location filename="../ui/main_dialog.py" line="2533" />
      <source>Follow QGIS</source>
      <translation>跟随 QGIS</translation>
    </message>
    <message>
      <location filename="../ui/language_picker.py" line="73" />
      <source>The language this dialog is shown in</source>
      <translation>此对话框的显示语言</translation>
    </message>
    <message>
      <location filename="../ui/language_picker.py" line="142" />
      <source>No thanks</source>
      <translation>不用了</translation>
    </message>
    <message>
      <location filename="../ui/language_picker.py" line="52" />
      <source>Language not downloaded</source>
      <translation>语言未下载</translation>
    </message>
    <message>
      <location filename="../ui/language_picker.py" line="139" />
      <source>Use {language}</source>
      <translation>使用{language}</translation>
    </message>
    <message>
      <location filename="../ui/language_picker.py" line="91" />
      <source>{language} - download</source>
      <translation>{language} - 下载</translation>
    </message>
    <message>
      <location filename="../ui/language_picker.py" line="134" />
      <source>This dialog is available in {language}.</source>
      <translation>此对话框提供{language}版本。</translation>
    </message>
    <message>
      <location filename="../ui/language_picker.py" line="53" />
      <source>{language} could not be downloaded.</source>
      <translation>无法下载{language}。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="161" />
      <source>OpenStreetMap's tile servers refuse maps opened straight from a file, so recipients who double-click this map may see 'Access blocked' tiles. Choose Liberty - the same OpenStreetMap data - or another basemap, or export as a Folder for a website.</source>
      <translation>OpenStreetMap 的瓦片服务器会拒绝直接从文件打开的地图，因此双击打开此地图的接收者可能会看到“Access blocked”瓦片。请选择 Liberty（同样的 OpenStreetMap 数据）或其他底图，或导出为“文件夹”用于网站。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="518" />
      <source># Help

The bundled guides could not be found in this installation.

They are also online at &lt;{url}&gt;.
</source>
      <translation># 帮助

在此安装中找不到随附的指南。

也可以在线查看：&lt;{url}&gt;。
</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="174" />
      <source>Standalone HTML - one file, opens by double-click</source>
      <translation>独立 HTML - 单个文件，双击即可打开</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="175" />
      <source>Share ZIP - a zip to email or upload</source>
      <translation>分享用 ZIP - 用于邮件发送或上传的 ZIP</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="176" />
      <source>Folder - for publishing to a web server</source>
      <translation>文件夹 - 用于发布到网站服务器</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="182" />
      <source>Value only, no label</source>
      <translation>仅显示值，不显示标签</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="183" />
      <source>Label beside value - always show</source>
      <translation>标签在值旁边 - 始终显示</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="184" />
      <source>Label beside value - only if it has data</source>
      <translation>标签在值旁边 - 仅在有数据时</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="185" />
      <source>Label above value - always show</source>
      <translation>标签在值上方 - 始终显示</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="186" />
      <source>Label above value - only if it has data</source>
      <translation>标签在值上方 - 仅在有数据时</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="187" />
      <source>Do not show this field</source>
      <translation>不显示此字段</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="265" />
      <source>Top centre - clear of the controls</source>
      <translation>顶部居中 - 避开控件</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="266" />
      <source>Bottom centre - clear of the controls</source>
      <translation>底部居中 - 避开控件</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="267" />
      <source>Top left - shared with the layer switcher</source>
      <translation>左上 - 与图层切换器共用</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="268" />
      <source>Top right - shared with the legend</source>
      <translation>右上 - 与图例共用</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="269" />
      <source>Bottom left - shared with zoom and scale</source>
      <translation>左下 - 与缩放和比例尺共用</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="270" />
      <source>Bottom right - shared with the credit</source>
      <translation>右下 - 与版权信息共用</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="278" />
      <source>None - the export stays offline and contacts nobody</source>
      <translation>无 - 地图离线可用，不连接任何服务器</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="279" />
      <source>OpenStreetMap</source>
      <translation>OpenStreetMap</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="280" />
      <source>Positron - pale, for data on top</source>
      <translation>Positron - 浅色，便于突出数据</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="281" />
      <source>Dark Matter - dark background</source>
      <translation>Dark Matter - 深色背景</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="282" />
      <source>Voyager - general purpose</source>
      <translation>Voyager - 通用</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="283" />
      <source>Liberty - detailed street map</source>
      <translation>Liberty - 详细街道图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="284" />
      <source>Bright - high contrast</source>
      <translation>Bright - 高对比度</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="291" />
      <source>Flat - no elevation</source>
      <translation>平面 - 无高程</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="292" />
      <source>Global relief - tilts the map so it shows</source>
      <translation>全球地形 - 倾斜地图以显示地形</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="298" />
      <source>Normal</source>
      <translation>正常</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="299" />
      <source>Small</source>
      <translation>小</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="300" />
      <source>Large</source>
      <translation>大</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="301" />
      <source>Extra large</source>
      <translation>特大</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="302" />
      <source>Largest - for presentations and big screens</source>
      <translation>最大 - 用于演示和大屏幕</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="306" />
      <source>The data - every feature is visible</source>
      <translation>数据范围 - 所有要素可见</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="307" />
      <source>The current QGIS view</source>
      <translation>当前 QGIS 视图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="378" />
      <source>Show in file manager</source>
      <translation>在文件管理器中显示</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="403" />
      <source>{n} day ago</source>
      <translation>{n} 天前</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="403" />
      <source>{n} days ago</source>
      <translation>{n} 天前</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="375" />
      <source>Reveal in Finder</source>
      <translation>在访达中显示</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="377" />
      <source>Show in Explorer</source>
      <translation>在资源管理器中显示</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="394" />
      <source>just now</source>
      <translation>刚刚</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="402" />
      <source>{n} hour ago</source>
      <translation>{n} 小时前</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="402" />
      <source>{n} hours ago</source>
      <translation>{n} 小时前</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="431" />
      <source>What these columns do</source>
      <translation>这些列的作用</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="507" />
      <source>Overview</source>
      <translation>概览</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="508" />
      <source>Your first export</source>
      <translation>第一次导出</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="509" />
      <source>The dialog, tab by tab</source>
      <translation>对话框各标签页说明</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="510" />
      <source>Sharing a map</source>
      <translation>分享地图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="511" />
      <source>Enhance a map with AI</source>
      <translation>用 AI 增强地图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="512" />
      <source>Putting a map online</source>
      <translation>将地图发布到网上</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="513" />
      <source>What gets exported</source>
      <translation>会导出哪些内容</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="514" />
      <source>Troubleshooting</source>
      <translation>故障排除</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="515" />
      <source>Privacy</source>
      <translation>隐私</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1365" />
      <source>{size} MB - {n} layer</source>
      <translation>{size} MB - {n} 个图层</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1365" />
      <source>{size} MB - {n} layers</source>
      <translation>{size} MB - {n} 个图层</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="637" />
      <source>QGIS2WebMap by NIKA</source>
      <translation>QGIS2WebMap by NIKA</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="686" />
      <source>Map</source>
      <translation>地图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="687" />
      <source>Layers</source>
      <translation>图层</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="688" />
      <location filename="../ui/main_dialog.py" line="2529" />
      <source>Appearance</source>
      <translation>外观</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="691" />
      <source>Fidelity</source>
      <translation>保真度</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="693" />
      <source>Help</source>
      <translation>帮助</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="707" />
      <source>Settings</source>
      <translation>设置</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="817" />
      <source>&lt;b&gt;Source:&lt;/b&gt; the layers already open in this QGIS project. There is nothing to browse for - choose which of them to include on the Layers tab.</source>
      <translation>&lt;b&gt;来源：&lt;/b&gt;此 QGIS 项目中已打开的图层。无需浏览文件——在“图层”标签页选择要包含的图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="835" />
      <source>The title shown on the exported map. Leave blank to use the project title.</source>
      <translation>导出地图上显示的标题。留空则使用项目标题。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="841" />
      <source>Map name</source>
      <translation>地图名称</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="857" />
      <location filename="../ui/main_dialog.py" line="2310" />
      <source>Export</source>
      <translation>导出</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="878" />
      <source>Choose where the export is written</source>
      <translation>选择导出内容的保存位置</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="882" />
      <source>Choose...</source>
      <translation>选择...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="913" />
      <source>Basemap</source>
      <translation>底图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="922" />
      <source>Show behind the map</source>
      <translation>在地图下方显示</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="940" />
      <source>Ground surface</source>
      <translation>地表</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="957" />
      <source>Open the map on</source>
      <translation>打开地图时显示</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="974" />
      <source>Export only the features in this view</source>
      <translation>仅导出此视图中的要素</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="996" />
      <source>Maintain full precision</source>
      <translation>保留全部精度</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1006" />
      <source>Rounding coordinates makes the file smaller and is irreversible. Around 6 decimal places is roughly 0.1 m at the equator.</source>
      <translation>对坐标取整可减小文件，但不可恢复。约 6 位小数在赤道处约为 0.1 米。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1012" />
      <source>Coordinate precision</source>
      <translation>坐标精度</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1092" />
      <source>Export makes a zip to email or upload, carrying {n} layer.</source>
      <translation>导出会生成一个用于邮件发送或上传的 ZIP，包含 {n} 个图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1092" />
      <source>Export makes a zip to email or upload, carrying {n} layers.</source>
      <translation>导出会生成一个用于邮件发送或上传的 ZIP，包含 {n} 个图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1314" />
      <source>Web page (*.html)</source>
      <translation>网页 (*.html)</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1316" />
      <source>Zip archive (*.zip)</source>
      <translation>ZIP 压缩包 (*.zip)</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1318" />
      <location filename="../ui/main_dialog.py" line="875" />
      <source>Save to</source>
      <translation>保存到</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1494" />
      <source>Layers come from the QGIS Layers panel and follow it live - reorder or rename them there and this list updates. Expand a layer to choose how each of its fields appears in popups.</source>
      <translation>图层来自 QGIS 的“图层”面板并实时同步——在那里重新排序或重命名，此列表会随之更新。展开图层可选择每个字段在弹窗中的显示方式。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1521" />
      <source>&lt;b&gt;Include&lt;/b&gt; - untick to leave the layer out of the map entirely. Its data is not written, so the file gets smaller.&lt;br&gt;&lt;b&gt;Popups&lt;/b&gt; - untick and clicking a feature does nothing. The attribute values are left out of the file entirely, so this is also how you keep data out of a map you are sending someone.&lt;br&gt;&lt;b&gt;Labels&lt;/b&gt; - untick to drop the text QGIS draws beside features. The features themselves still appear.&lt;br&gt;Unticking Popups or Labels only affects that layer; the map still works, with less on it. Expand a layer to hide single fields instead - those values leave the file too, unless the map draws with them.</source>
      <translation>&lt;b&gt;包含&lt;/b&gt; - 取消勾选可将该图层完全排除在地图之外。其数据不会写入，文件因此更小。&lt;br&gt;&lt;b&gt;弹窗&lt;/b&gt; - 取消勾选后，点击要素不会有任何反应。属性值会完全排除在文件之外，所以这也是让数据不进入你要发送的地图的方法。&lt;br&gt;&lt;b&gt;标注&lt;/b&gt; - 取消勾选可去掉 QGIS 在要素旁绘制的文字。要素本身仍会显示。&lt;br&gt;取消勾选“弹窗”或“标注”只影响该图层；地图照常工作，只是内容更少。展开图层可隐藏单个字段——这些值也会离开文件，除非地图需要用它们来绘制。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1552" />
      <source>Apply to all layers</source>
      <translation>应用到所有图层</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1554" />
      <source>Give every popup field in every layer this setting, replacing the choices below.</source>
      <translation>将此设置应用到所有图层的所有弹窗字段，替换下方的选择。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1725" />
      <source>This layer only</source>
      <translation>仅此图层</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1743" />
      <source>Precision: same as map</source>
      <translation>精度：与地图相同</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1744" />
      <source>Precision: keep it all</source>
      <translation>精度：全部保留</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1766" />
      <source>Highlight: same as map</source>
      <translation>高亮：与地图相同</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1767" />
      <source>Highlight colour for this layer only.</source>
      <translation>仅用于此图层的高亮颜色。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1855" />
      <source>Map controls</source>
      <translation>地图控件</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1886" />
      <source>Caption</source>
      <translation>页眉</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1889" />
      <source>Map title</source>
      <translation>地图标题</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1904" />
      <source>Project description</source>
      <translation>项目描述</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1925" />
      <source>Position</source>
      <translation>位置</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1941" />
      <source>Control colours</source>
      <translation>控件颜色</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1946" />
      <location filename="../ui/main_dialog.py" line="1955" />
      <source>Default</source>
      <translation>默认</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1951" />
      <source>Background</source>
      <translation>背景</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1960" />
      <source>Text and icons</source>
      <translation>文字和图标</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1967" />
      <source>Size</source>
      <translation>大小</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1992" />
      <source>Behaviour</source>
      <translation>行为</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1995" />
      <source>Open popups on hover instead of click</source>
      <translation>悬停时打开弹窗，而不是点击</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1998" />
      <source>Hover replaces click rather than adding to it: bound together, a click on an already-open popup appears to do nothing.</source>
      <translation>悬停会取代点击，而不是与之并存：两者同时启用时，点击已打开的弹窗看起来没有任何反应。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2020" />
      <source>Default (white, see-through)</source>
      <translation>默认（白色，半透明）</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2022" />
      <source>Shown when the cursor is over a feature. Keep some transparency: a solid colour hides whatever the feature is drawn on top of.</source>
      <translation>光标悬停在要素上时显示。请保留一些透明度：纯色会遮住要素下方的内容。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2201" />
      <source>Checking the project...</source>
      <translation>正在检查项目...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2230" />
      <source>That layer is no longer in the project.</source>
      <translation>该图层已不在项目中。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2278" />
      <location filename="../ui/main_dialog.py" line="2353" />
      <source>Preview</source>
      <translation>预览</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2297" />
      <source>Share</source>
      <translation>分享</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2313" />
      <source>Write the map in the type and place chosen on the Map tab</source>
      <translation>按“地图”标签页中选择的类型和位置写入地图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2399" />
      <source>Copy file path</source>
      <translation>复制文件路径</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2400" />
      <source>Copies the map file's path to the clipboard</source>
      <translation>将地图文件路径复制到剪贴板</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2406" />
      <source>Shows the map file in your file manager</source>
      <translation>在文件管理器中显示地图文件</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2410" />
      <source>Publish</source>
      <translation>发布</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2413" />
      <source>Publish with NIKA hosting... (waitlist)</source>
      <translation>使用 NIKA 托管发布...（候补名单）</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2414" />
      <source>Hosting is not live yet. This opens a short form so we can see how many people want it.</source>
      <translation>托管服务尚未上线。这会打开一个简短表单，帮助我们了解有多少人需要它。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2425" />
      <source>NIKA</source>
      <translation>NIKA</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2428" />
      <source>Enhance with NIKA AI...</source>
      <translation>用 NIKA AI 增强...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2429" />
      <source>Opens a hand-off dialog with a prompt to paste into NIKA</source>
      <translation>打开一个交接对话框，其中有可粘贴到 NIKA 的提示词</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2434" />
      <source>Ask NIKA for a critique...</source>
      <translation>请 NIKA 点评...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2435" />
      <source>Opens a hand-off dialog asking NIKA to review the map as a cartographer would</source>
      <translation>打开一个交接对话框，请 NIKA 像制图师一样审阅地图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2444" />
      <source>Make a cartograph with QGIS2Carto...</source>
      <translation>用 QGIS2Carto 制作地图海报...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2445" />
      <source>Opens QGIS2Carto with this map as a live frame</source>
      <translation>在 QGIS2Carto 中以实时画框打开此地图</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2450" />
      <source>Ask on Discord ↗</source>
      <translation>在 Discord 上提问 ↗</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2452" />
      <source>Open the QGIS2WebMap channel on the NIKA Discord in your browser. Ask a question, suggest something, or report a bug - no GitHub account needed.</source>
      <translation>在浏览器中打开 NIKA Discord 上的 QGIS2WebMap 频道。提问、提建议或报告错误——无需 GitHub 账户。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2459" />
      <source>More ways to share are coming soon</source>
      <translation>更多分享方式即将推出</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2493" />
      <source>NIKA account</source>
      <translation>NIKA 账户</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2498" />
      <source>Account</source>
      <translation>账户</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2511" />
      <source>Language</source>
      <translation>语言</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2515" />
      <source>Show this dialog in</source>
      <translation>对话框显示语言</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2542" />
      <source>Theme</source>
      <translation>主题</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2548" />
      <source>Plugin version</source>
      <translation>插件版本</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2551" />
      <location filename="../ui/runtime_setup.py" line="248" />
      <location filename="../ui/runtime_setup.py" line="202" />
      <location filename="../ui/runtime_setup.py" line="263" />
      <source>OnlyMap runtime</source>
      <translation>OnlyMap 运行时</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2587" />
      <source>Open the full documentation</source>
      <translation>打开完整文档</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2591" />
      <source>Join the community</source>
      <translation>加入社区</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2593" />
      <source>Open the NIKA Discord in your browser. Problems, suggestions and bug reports all go to the QGIS2WebMap channel - no GitHub account needed.</source>
      <translation>在浏览器中打开 NIKA 的 Discord。问题、建议和错误报告都发到 QGIS2WebMap 频道——无需 GitHub 账户。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2619" />
      <source>Close</source>
      <translation>关闭</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2642" />
      <location filename="../ui/runtime_setup.py" line="243" />
      <source>Cancel</source>
      <translation>取消</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2726" />
      <source>Stopping...</source>
      <translation>正在停止...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2744" />
      <source>Stopped. Nothing was written.</source>
      <translation>已停止。未写入任何内容。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2769" />
      <source>Something went wrong</source>
      <translation>出现错误</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2860" />
      <location filename="../ui/main_dialog.py" line="2851" />
      <source>Live preview stopped.</source>
      <translation>实时预览已停止。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2926" />
      <location filename="../ui/main_dialog.py" line="2924" />
      <location filename="../ui/main_dialog.py" line="2913" />
      <source>Updating the preview...</source>
      <translation>正在更新预览...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3152" />
      <source>The OnlyMap runtime is needed to build a map. Nothing was written.</source>
      <translation>构建地图需要 OnlyMap 运行时。未写入任何内容。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3168" />
      <location filename="../ui/main_dialog.py" line="3360" />
      <location filename="../ui/main_dialog.py" line="3099" />
      <source>Reading the project...</source>
      <translation>正在读取项目...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3228" />
      <location filename="../ui/main_dialog.py" line="3189" />
      <source>Building the preview...</source>
      <translation>正在构建预览...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3247" />
      <source>Live preview could not start on this machine, so the preview opened as a file. Reload the tab after making changes.</source>
      <translation>实时预览无法在本机启动，因此预览以文件形式打开。修改后请刷新标签页。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3346" />
      <source>Hosting is not live yet - the waitlist form opened in your browser.</source>
      <translation>托管服务尚未上线——候补名单表单已在浏览器中打开。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3483" />
      <location filename="../ui/main_dialog.py" line="3419" />
      <source>Writing the map...</source>
      <translation>正在写入地图...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3494" />
      <source>This will be a large single file</source>
      <translation>这将是一个很大的单一文件</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3497" />
      <source>The map file inlines all the data into the one HTML file, which is why it is this big. Exporting as Share ZIP on the Map tab packages the same map to send more easily.</source>
      <translation>地图文件把所有数据都内嵌在一个 HTML 文件中，所以才这么大。在“地图”标签页中导出为“分享用 ZIP”会打包同一张地图，更便于发送。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3504" />
      <source>Write it anyway</source>
      <translation>仍然写入</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3543" />
      <source>Choose where to save first</source>
      <translation>请先选择保存位置</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3544" />
      <source>This map has nowhere to be saved yet.

Fill in Save to on the Map tab, then try again.</source>
      <translation>此地图还没有保存位置。

请在“地图”标签页填写“保存到”，然后重试。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3568" />
      <location filename="../ui/main_dialog.py" line="3436" />
      <source>Cannot export</source>
      <translation>无法导出</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="396" />
      <source>{n} s ago</source>
      <translation>{n} 秒前</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="399" />
      <source>{n} min ago</source>
      <translation>{n} 分钟前</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="845" />
      <source>Shown on the map and in the browser tab. Leave blank to use the project title.</source>
      <translation>显示在地图上和浏览器标签页中。留空则使用项目标题。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="863" />
      <source>Export as</source>
      <translation>导出为</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="961" />
      <source>Where the map is positioned when someone opens it. They can still pan and zoom anywhere afterwards.</source>
      <translation>别人打开地图时地图所在的位置。之后仍可随意平移和缩放。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="982" />
      <source>Leaves out everything outside the current QGIS view. This is the practical way to bring a very large layer under the free plan's 25,000-feature limit. The Fidelity tab reports how many features each layer loses.</source>
      <translation>排除当前 QGIS 视图以外的所有内容。这是让超大图层符合免费方案 25,000 个要素上限的实用方法。“保真度”标签页会列出每个图层损失的要素数量。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="999" />
      <source>{n} decimal place</source>
      <translation>{n} 位小数</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="999" />
      <source>{n} decimal places</source>
      <translation>{n} 位小数</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1018" />
      <source>Rounding makes the file smaller and cannot be undone. 6 places is about 0.1 m at the equator.</source>
      <translation>取整可减小文件且无法撤销。6 位小数在赤道处约为 0.1 米。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1098" />
      <source>Export makes a folder for a web server, carrying {n} layer.</source>
      <translation>导出会生成一个用于网站服务器的文件夹，包含 {n} 个图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1098" />
      <source>Export makes a folder for a web server, carrying {n} layers.</source>
      <translation>导出会生成一个用于网站服务器的文件夹，包含 {n} 个图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1104" />
      <source>Export makes one HTML file that opens by double-click, carrying {n} layer.</source>
      <translation>导出会生成一个双击即可打开的 HTML 文件，包含 {n} 个图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1104" />
      <source>Export makes one HTML file that opens by double-click, carrying {n} layers.</source>
      <translation>导出会生成一个双击即可打开的 HTML 文件，包含 {n} 个图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1308" />
      <source>Export folder for hosting</source>
      <translation>导出用于托管的文件夹</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1340" />
      <source>Choose where to save, then press Export.</source>
      <translation>选择保存位置，然后点击“导出”。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1344" />
      <source>Not written yet.</source>
      <translation>尚未写入。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1456" />
      <source>The map file inlines every feature, so it carries all the data itself. Past about {limit} MB it gets slow to open and awkward to move around.</source>
      <translation>地图文件内嵌所有要素，因此自带全部数据。超过约 {limit} MB 后，打开会变慢，也不便传输。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1508" />
      <source>Layer</source>
      <translation>图层</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1508" />
      <source>Include</source>
      <translation>包含</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1508" />
      <source>Popups</source>
      <translation>弹窗</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1543" />
      <source>Set every field to</source>
      <translation>将所有字段设为</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1731" />
      <source>Popups: same as map</source>
      <translation>弹窗：与地图相同</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1732" />
      <source>Popups: on hover</source>
      <translation>弹窗：悬停时</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1733" />
      <source>Popups: on click</source>
      <translation>弹窗：点击时</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1747" />
      <source>Precision: {n} decimal place</source>
      <translation>精度：{n} 位小数</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1747" />
      <source>Precision: {n} decimal places</source>
      <translation>精度：{n} 位小数</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1847" />
      <source>These are on by default - an exported map should be useful without configuring anything.</source>
      <translation>默认全部开启——导出的地图应当无需任何配置即可使用。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1896" />
      <source>Draws the map name over the map. The legend drops its own heading while this is on, so the title is not shown twice.</source>
      <translation>在地图上绘制地图名称。开启时图例会省略自己的标题，避免标题重复显示。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1911" />
      <source>The abstract from Project Properties &gt; Metadata. Nothing appears if the project has none.</source>
      <translation>来自“项目属性 &gt; 元数据”中的摘要。如果项目没有摘要，则不显示任何内容。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1929" />
      <source>All four corners already hold map controls - switcher top left, legend top right, zoom and scale bottom left, credit bottom right. The centres are the only clear space.</source>
      <translation>四个角都已放置地图控件——左上为切换器，右上为图例，左下为缩放和比例尺，右下为版权信息。只有中间位置是空的。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1971" />
      <source>Scales the legend, layer switcher, zoom controls, scale bar and title together. The credit stays fixed so attribution cannot be shrunk out of legibility.</source>
      <translation>同时缩放图例、图层切换器、缩放控件、比例尺和标题。版权信息保持固定，以免署名缩小到无法辨认。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1982" />
      <source>Applies to the legend, layer switcher, zoom controls and scale bar. Leave both unset to keep the map's own styling.</source>
      <translation>应用于图例、图层切换器、缩放控件和比例尺。两项都不设置则保留地图本身的样式。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2007" />
      <source>Hover replaces click rather than adding to it.</source>
      <translation>悬停会取代点击，而不是与之并存。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2017" />
      <source>Highlight under the cursor</source>
      <translation>光标下的高亮</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2340" />
      <source>Live preview is on: Preview keeps the browser tab updating as you change settings. Click to turn it off.</source>
      <translation>实时预览已开启：预览会随你更改设置而持续更新浏览器标签页。点击即可关闭。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2345" />
      <source>Live preview is off: Preview opens the map as it is now. Click to keep the tab updating as you change settings.</source>
      <translation>实时预览已关闭：预览会打开地图的当前状态。点击后，标签页会随你更改设置而持续更新。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2353" />
      <source>Live</source>
      <translation>实时</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2355" />
      <source>Live preview is running. Press to stop it.</source>
      <translation>实时预览正在运行。按下即可停止。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2357" />
      <source>Open the map in your browser.</source>
      <translation>在浏览器中打开地图。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2502" />
      <source>Publishing maps to NIKA needs an account. Exporting a file never does.</source>
      <translation>向 NIKA 发布地图需要账户。导出文件从不需要。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2519" />
      <source>English is built in. Other languages are a small download the first time you pick one. Follow QGIS uses the language QGIS is set to, when it is one of these.</source>
      <translation>英语为内置语言。其他语言在你首次选择时需要少量下载。“跟随 QGIS”会在 QGIS 设置的语言属于以下之一时使用该语言。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2534" />
      <source>Light</source>
      <translation>浅色</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2535" />
      <source>Dark</source>
      <translation>深色</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2670" />
      <source>Still working on the last request.</source>
      <translation>仍在处理上一个请求。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2870" />
      <source>Preview tab closed, so the preview server stopped. Press Preview to start it again.</source>
      <translation>预览标签页已关闭，预览服务器已停止。点击“预览”可重新启动。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2976" />
      <source>Add a vector or raster layer to the project to export.</source>
      <translation>请向项目添加矢量或栅格图层以进行导出。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3225" />
      <source>Live preview open. Changes here update the tab automatically.</source>
      <translation>实时预览已打开。在这里的更改会自动更新标签页。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3429" />
      <source>Finishing...</source>
      <translation>正在完成...</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3530" />
      <source>Folder not found</source>
      <translation>未找到文件夹</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3564" />
      <source>No layer in the selection could be read.</source>
      <translation>所选图层均无法读取。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3573" />
      <source>Fix these in QGIS, then export again. The Fidelity tab lists everything that was checked.</source>
      <translation>请在 QGIS 中修正这些问题后再次导出。“保真度”标签页列出了所有检查项。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="354" />
      <source>&lt;p&gt;Details are in the QGIS message log, under &lt;b&gt;{tag}&lt;/b&gt;.&lt;/p&gt;&lt;p&gt;Tell us about it in the QGIS2WebMap channel on &lt;a href="{discord}"&gt;our Discord&lt;/a&gt;, or &lt;a href="{issues}"&gt;open a GitHub issue&lt;/a&gt;. Please include the log.&lt;/p&gt;</source>
      <translation>&lt;p&gt;详细信息在 QGIS 消息日志的 &lt;b&gt;{tag}&lt;/b&gt; 下。&lt;/p&gt;&lt;p&gt;请在&lt;a href="{discord}"&gt;我们的 Discord&lt;/a&gt; 的 QGIS2WebMap 频道告诉我们，或&lt;a href="{issues}"&gt;在 GitHub 提交 issue&lt;/a&gt;。请附上日志。&lt;/p&gt;</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1353" />
      <source>The map file cannot be read from here.</source>
      <translation>无法从这里读取地图文件。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1421" />
      <source>Choose where to save on the Map tab first</source>
      <translation>请先在“地图”标签页选择保存位置</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2430" />
      <source>Enhance with NIKA AI</source>
      <translation>用 NIKA AI 增强</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2439" />
      <source>Ask NIKA for a critique</source>
      <translation>请 NIKA 点评</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2446" />
      <source>Make a cartograph with QGIS2Carto</source>
      <translation>用 QGIS2Carto 制作地图海报</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2473" />
      <source>{what} is not built yet</source>
      <translation>{what} 尚未完成</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2474" />
      <source>{what} is coming in a later release.

The menu entry is here so the shape of the Share menu can be reviewed; pressing it does nothing else yet.</source>
      <translation>{what} 将在后续版本中推出。

此菜单项在这里是为了方便评审“分享”菜单的结构；目前点击它不会做其他事情。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2737" />
      <source>Export failed</source>
      <translation>导出失败</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2755" />
      <source>Live preview could not be updated ({message}) - see the QGIS log.</source>
      <translation>无法更新实时预览（{message}）——请查看 QGIS 日志。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2922" />
      <source>Live preview updated.</source>
      <translation>实时预览已更新。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2979" />
      <source>Tick at least one layer to include.</source>
      <translation>请至少勾选一个要包含的图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2984" />
      <source>{n} layer will be exported.</source>
      <translation>将导出 {n} 个图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2984" />
      <source>{n} layers will be exported.</source>
      <translation>将导出 {n} 个图层。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3196" />
      <location filename="../ui/main_dialog.py" line="3216" />
      <source>Preview opened. Press Preview again after making changes - your position on the map is kept.</source>
      <translation>预览已打开。更改后再次按预览即可 - 你在地图上的位置会保留。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3311" />
      <source>Copied {path}</source>
      <translation>已复制：{path}</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3397" />
      <source>Choose Folder or Share ZIP</source>
      <translation>请选择文件夹或分享用 ZIP</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3464" />
      <source>Exported, with warnings</source>
      <translation>已导出，但有警告</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1117" />
      <source>To: {path}</source>
      <translation>保存到：{path}</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1379" />
      <source>Last written {ago} - {detail}. The project has changed since; Export writes it again.</source>
      <translation>上次写入于 {ago} - {detail}。此后项目已更改；“导出”会重新写入。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1386" />
      <source>Last written {ago} - {detail}</source>
      <translation>上次写入于 {ago} - {detail}</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1472" />
      <source>This map's data is {size} MB, past the {limit} MB where a single file starts to be unwieldy. The map file is still written - exporting as Share ZIP is the practical way to send something this size.</source>
      <translation>此地图的数据为 {size} MB，超过了 {limit} MB，单个文件会变得难以处理。地图文件仍会写入——导出为“分享用 ZIP”是发送这种大小文件的实用方式。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="2552" />
      <source>not pinned</source>
      <translation>未固定</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3043" />
      <source>Reading '{name}' ({done} of {total})</source>
      <translation>正在读取“{name}”（第 {done} 个，共 {total} 个）</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3471" />
      <source>The Fidelity tab lists the detail.</source>
      <translation>详情请见“保真度”标签页。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3481" />
      <source>Export complete</source>
      <translation>导出完成</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3531" />
      <source>There is no folder at {path}.

Pick somewhere that exists, or create it first.</source>
      <translation>{path} 处没有文件夹。

请选择一个已存在的位置，或先创建它。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="1484" />
      <source>This map's data is {size} MB.</source>
      <translation>此地图的数据为 {size} MB。</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3437" />
      <location filename="../ui/main_dialog.py" line="3569" />
      <source>This map cannot be exported yet:</source>
      <translation>此地图暂时无法导出：</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3331" />
      <source>Opened {path}</source>
      <translation>已打开：{path}</translation>
    </message>
    <message>
      <location filename="../ui/main_dialog.py" line="3467" />
      <source>The exported map has known limitations:</source>
      <translation>导出的地图存在已知限制：</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="56" />
      <source>&lt;p&gt;&lt;b&gt;QGIS2WebMap needs the OnlyMap runtime to build a map.&lt;/b&gt;&lt;/p&gt;&lt;p&gt;The runtime is the code that draws the map in a browser. It is embedded in every map you export, which is what lets someone open your map with no internet connection and no software installed.&lt;/p&gt;&lt;p&gt;It is downloaded &lt;b&gt;once on this computer&lt;/b&gt; ({size}). Every export after that works offline.&lt;/p&gt;&lt;p&gt;QGIS2WebMap is free and open source. The OnlyMap runtime is a separate commercial product by NIKA with its own licence, shown below.&lt;/p&gt;</source>
      <translation>&lt;p&gt;&lt;b&gt;QGIS2WebMap 需要 OnlyMap 运行时来生成地图。&lt;/b&gt;&lt;/p&gt;&lt;p&gt;运行时是在浏览器中绘制地图的代码。它会嵌入你导出的每张地图中，因此别人无需联网、无需安装任何软件即可打开你的地图。&lt;/p&gt;&lt;p&gt;它只需&lt;b&gt;在这台电脑上下载一次&lt;/b&gt;（{size}）。之后的每次导出都可离线进行。&lt;/p&gt;&lt;p&gt;QGIS2WebMap 是免费开源软件。OnlyMap 运行时是 NIKA 的独立商业产品，有其自己的许可证，见下文。&lt;/p&gt;</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="73" />
      <source>Install the OnlyMap runtime</source>
      <translation>安装 OnlyMap 运行时</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="99" />
      <source>I accept the OnlyMap licence</source>
      <translation>我接受 OnlyMap 许可协议</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="107" />
      <source>Download and install</source>
      <translation>下载并安装</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="203" />
      <source>This installation does not pin a runtime version, so the runtime cannot be downloaded. Please reinstall the plugin.</source>
      <translation>此安装未固定运行时版本，因此无法下载运行时。请重新安装插件。</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="147" />
      <source>The download was cancelled.</source>
      <translation>下载已取消。</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="240" />
      <source>Downloading the OnlyMap runtime ({size})…</source>
      <translation>正在下载 OnlyMap 运行时（{size}）…</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="153" />
      <source>If this computer reaches the internet through a proxy, check Settings -&gt; Options -&gt; Network in QGIS.</source>
      <translation>如果这台电脑通过代理访问互联网，请检查 QGIS 中的 设置 -&gt; 选项 -&gt; 网络。</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="151" />
      <source>Could not download the OnlyMap runtime.</source>
      <translation>无法下载 OnlyMap 运行时。</translation>
    </message>
    <message>
      <location filename="../ui/runtime_setup.py" line="85" />
      <source>Source: {package}</source>
      <translation>来源：{package}</translation>
    </message>
  </context>
</TS>
