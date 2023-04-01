import QtQuick 2.6
import QtQuick.Layouts 1.0
import QtQuick.Controls 2.3
import QtQuick.Controls.Material 2.0

import org.electrum 1.0

import "controls"

Pane {
    id: root
    width: parent.width
    height: parent.height
    padding: 0

    property string txid
    property string rawtx

    property alias label: txdetails.label

    signal detailsChanged

    function close() {
        app.stack.pop()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true

            contentHeight: flickableRoot.height
            clip: true
            interactive: height < contentHeight

            Pane {
                id: flickableRoot
                width: parent.width
                padding: constants.paddingLarge

                GridLayout {
                    width: parent.width
                    columns: 2

                    Heading {
                        Layout.columnSpan: 2
                        text: qsTr('Transaction Details')
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.columnSpan: 2
                        visible: txdetails.isUnrelated
                        Image {
                            source: '../../icons/warning.png'
                            Layout.preferredWidth: constants.iconSizeSmall
                            Layout.preferredHeight: constants.iconSizeSmall
                        }
                        Label {
                            text: qsTr('Transaction is unrelated to this wallet')
                            color: Material.accentColor
                        }
                    }

                    Label {
                        visible: !txdetails.isUnrelated && txdetails.lnAmount.satsInt == 0
                        text: txdetails.amount.satsInt > 0
                                ? qsTr('Amount received')
                                : qsTr('Amount sent')
                        color: Material.accentColor
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: !txdetails.isUnrelated && txdetails.lnAmount.satsInt != 0
                        text: txdetails.lnAmount.satsInt > 0
                                ? qsTr('Amount received in channels')
                                : qsTr('Amount withdrawn from channels')
                        color: Material.accentColor
                        wrapMode: Text.Wrap
                    }

                    FormattedAmount {
                        visible: !txdetails.isUnrelated
                        Layout.fillWidth: true
                        amount: txdetails.lnAmount.isEmpty ? txdetails.amount : txdetails.lnAmount
                    }

                    Label {
                        visible: !txdetails.fee.isEmpty
                        text: qsTr('Transaction fee')
                        color: Material.accentColor
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: !txdetails.fee.isEmpty
                        FormattedAmount {
                            Layout.fillWidth: true
                            amount: txdetails.fee
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr('Status')
                        color: Material.accentColor
                    }

                    Label {
                        Layout.fillWidth: true
                        text: txdetails.status
                    }

                    Label {
                        text: qsTr('Mempool depth')
                        color: Material.accentColor
                        visible: txdetails.mempoolDepth
                    }

                    Label {
                        text: txdetails.mempoolDepth
                        visible: txdetails.mempoolDepth
                    }

                    TextHighlightPane {
                        Layout.fillWidth: true
                        Layout.topMargin: constants.paddingSmall
                        Layout.columnSpan: 2
                        borderColor: constants.colorWarning
                        visible: txdetails.canBump || txdetails.canCpfp || txdetails.canCancel

                        GridLayout {
                            width: parent.width
                            columns: actionButtonsLayout.implicitWidth > parent.width/2
                                ? 1
                                : 2
                            Label {
                                id: bumpfeeinfo
                                Layout.fillWidth: true
                                text: qsTr('This transaction is still unconfirmed.') + '\n' + (txdetails.canCancel
                                    ? qsTr('You can increase fees to speed up the transaction, or cancel this transaction')
                                    : qsTr('You can increase fees to speed up the transaction'))
                                wrapMode: Text.Wrap
                            }
                            ColumnLayout {
                                id: actionButtonsLayout
                                Layout.alignment: Qt.AlignHCenter
                                Pane {
                                    Layout.alignment: Qt.AlignHCenter
                                    background: Rectangle { color: Material.dialogColor }
                                    padding: 0
                                    visible: txdetails.canBump || txdetails.canCpfp
                                    FlatButton {
                                        id: feebumpButton
                                        textUnderIcon: false
                                        icon.source: '../../icons/add.png'
                                        text: qsTr('Bump fee')
                                        onClicked: {
                                            if (txdetails.canBump) {
                                                var dialog = rbfBumpFeeDialog.createObject(root, { txid: root.txid })
                                            } else {
                                                var dialog = cpfpBumpFeeDialog.createObject(root, { txid: root.txid })
                                            }
                                            dialog.open()
                                        }
                                    }
                                }
                                Pane {
                                    Layout.alignment: Qt.AlignHCenter
                                    background: Rectangle { color: Material.dialogColor }
                                    padding: 0
                                    visible: txdetails.canCancel
                                    FlatButton {
                                        id: cancelButton
                                        textUnderIcon: false
                                        icon.source: '../../icons/closebutton.png'
                                        text: qsTr('Cancel Tx')
                                        onClicked: {
                                            var dialog = rbfCancelDialog.createObject(root, { txid: root.txid })
                                            dialog.open()
                                        }
                                    }
                                }
                            }
                        }

                    }

                    Label {
                        visible: txdetails.isMined
                        text: qsTr('Date')
                        color: Material.accentColor
                    }

                    Label {
                        visible: txdetails.isMined
                        text: txdetails.date
                    }

                    Label {
                        visible: txdetails.isMined
                        text: qsTr('Mined at')
                        color: Material.accentColor
                    }

                    Label {
                        visible: txdetails.isMined
                        text: txdetails.shortId
                    }

                    Label {
                        Layout.columnSpan: 2
                        Layout.topMargin: constants.paddingSmall
                        text: qsTr('Label')
                        color: Material.accentColor
                    }

                    TextHighlightPane {
                        id: labelContent

                        property bool editmode: false

                        Layout.columnSpan: 2
                        Layout.fillWidth: true

                        RowLayout {
                            width: parent.width
                            Label {
                                visible: !labelContent.editmode
                                text: txdetails.label
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                                font.pixelSize: constants.fontSizeLarge
                            }
                            ToolButton {
                                visible: !labelContent.editmode
                                icon.source: '../../icons/pen.png'
                                icon.color: 'transparent'
                                onClicked: {
                                    labelEdit.text = txdetails.label
                                    labelContent.editmode = true
                                    labelEdit.focus = true
                                }
                            }
                            TextField {
                                id: labelEdit
                                visible: labelContent.editmode
                                text: txdetails.label
                                font.pixelSize: constants.fontSizeLarge
                                Layout.fillWidth: true
                            }
                            ToolButton {
                                visible: labelContent.editmode
                                icon.source: '../../icons/confirmed.png'
                                icon.color: 'transparent'
                                onClicked: {
                                    labelContent.editmode = false
                                    txdetails.set_label(labelEdit.text)
                                }
                            }
                            ToolButton {
                                visible: labelContent.editmode
                                icon.source: '../../icons/closebutton.png'
                                icon.color: 'transparent'
                                onClicked: labelContent.editmode = false
                            }
                        }
                    }

                    Label {
                        Layout.columnSpan: 2
                        Layout.topMargin: constants.paddingSmall
                        text: qsTr('Transaction ID')
                        color: Material.accentColor
                    }

                    TextHighlightPane {
                        Layout.columnSpan: 2
                        Layout.fillWidth: true

                        RowLayout {
                            width: parent.width
                            Label {
                                text: txdetails.txid
                                font.pixelSize: constants.fontSizeLarge
                                font.family: FixedFont
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                            }
                            ToolButton {
                                icon.source: '../../icons/share.png'
                                icon.color: 'transparent'
                                enabled: txdetails.txid
                                onClicked: {
                                    var dialog = app.genericShareDialog.createObject(root,
                                        { title: qsTr('Transaction ID'), text: txdetails.txid }
                                    )
                                    dialog.open()
                                }
                            }
                        }
                    }

                    Label {
                        Layout.columnSpan: 2
                        Layout.topMargin: constants.paddingSmall
                        text: qsTr('Outputs')
                        color: Material.accentColor
                    }

                    Repeater {
                        model: txdetails.outputs
                        delegate: TextHighlightPane {
                            Layout.columnSpan: 2
                            Layout.fillWidth: true

                            RowLayout {
                                width: parent.width
                                Label {
                                    text: modelData.address
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    font.pixelSize: constants.fontSizeLarge
                                    font.family: FixedFont
                                    color: modelData.is_mine ? constants.colorMine : Material.foreground
                                }
                                Label {
                                    text: Config.formatSats(modelData.value)
                                    font.pixelSize: constants.fontSizeMedium
                                    font.family: FixedFont
                                }
                                Label {
                                    text: Config.baseUnit
                                    font.pixelSize: constants.fontSizeMedium
                                    color: Material.accentColor
                                }
                            }
                        }
                    }
                }
            }
        }

        ButtonContainer {
            Layout.fillWidth: true

            FlatButton {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                icon.source: '../../icons/key.png'
                text: qsTr('Sign')
                visible: txdetails.canSign
                onClicked: txdetails.sign()
            }

            FlatButton {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                icon.source: '../../icons/microphone.png'
                text: qsTr('Broadcast')
                visible: txdetails.canBroadcast
                onClicked: txdetails.broadcast()
            }

            FlatButton {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                icon.source: '../../icons/qrcode_white.png'
                text: qsTr('Share')
                enabled: !txdetails.isUnrelated
                onClicked: {
                    var msg = ''
                    if (txdetails.isComplete) {
                        if (!txdetails.isMined && !txdetails.mempoolDepth) // local
                            // TODO: iff offline wallet?
                            // TODO: or also if just temporarily offline?
                            msg = qsTr('This transaction is complete. Please share it with an online device')
                    } else if (txdetails.wallet.isWatchOnly) {
                        msg = qsTr('This transaction should be signed. Present this QR code to the signing device')
                    } else if (txdetails.wallet.isMultisig && txdetails.wallet.walletType != '2fa') {
                        if (txdetails.canSign) {
                            msg = qsTr('Note: this wallet can sign, but has not signed this transaction yet')
                        } else {
                            msg = qsTr('Transaction is partially signed by this wallet. Present this QR code to the next co-signer')
                        }
                    }

                    app.stack.getRoot().showExport(txdetails.getSerializedTx(), msg)
                }
            }

            FlatButton {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                icon.source: '../../icons/save.png'
                text: qsTr('Save')
                visible: txdetails.canSaveAsLocal
                onClicked: txdetails.save()
            }

            FlatButton {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                icon.source: '../../icons/delete.png'
                text: qsTr('Remove')
                visible: txdetails.canRemove
                onClicked: txdetails.removeLocalTx()
            }

        }

    }

    TxDetails {
        id: txdetails
        wallet: Daemon.currentWallet
        txid: root.txid
        rawtx: root.rawtx
        onLabelChanged: root.detailsChanged()
        onConfirmRemoveLocalTx: {
            var dialog = app.messageDialog.createObject(app, { text: message, yesno: true })
            dialog.yesClicked.connect(function() {
                dialog.close()
                txdetails.removeLocalTx(true)
                root.close()
            })
            dialog.open()
        }
    }

    Connections {
        target: Daemon.currentWallet
        function onSaveTxSuccess(txid) {
            if (txid != txdetails.txid)
                return
            var dialog = app.messageDialog.createObject(app, {
                text: qsTr('Transaction added to wallet history.') + '\n\n' +
                      qsTr('Note: this is an offline transaction, if you want the network to see it, you need to broadcast it.')
            })
            dialog.open()
            root.close()
        }
        function onSaveTxError(txid, code, message) {
            if (txid != txdetails.txid)
                return
            var dialog = app.messageDialog.createObject(app, { text: message })
            dialog.open()
        }
        function onBroadcastSucceeded() {
            bumpfeeinfo.text = qsTr('Transaction was broadcast successfully')
            actionButtonsLayout.visible = false
        }
    }

    Component {
        id: rbfBumpFeeDialog
        RbfBumpFeeDialog {
            id: dialog
            rbffeebumper: TxRbfFeeBumper {
                id: rbffeebumper
                wallet: Daemon.currentWallet
                txid: dialog.txid
            }
            onTxaccepted: {
                root.rawtx = rbffeebumper.getNewTx()
                if (txdetails.wallet.canSignWithoutCosigner) {
                    txdetails.sign_and_broadcast()
                } else {
                    var dialog = app.messageDialog.createObject(app, {
                        text: qsTr('Transaction fee updated.') + '\n\n' + qsTr('You still need to sign and broadcast this transaction.')
                    })
                    dialog.open()
                }
            }
            onClosed: destroy()
        }
    }

    Component {
        id: cpfpBumpFeeDialog
        CpfpBumpFeeDialog {
            id: dialog
            cpfpfeebumper: TxCpfpFeeBumper {
                id: cpfpfeebumper
                wallet: Daemon.currentWallet
                txid: dialog.txid
            }

            onTxaccepted: {
                // replaces parent tx with cpfp tx
                root.rawtx = cpfpfeebumper.getNewTx()
                if (txdetails.wallet.canSignWithoutCosigner) {
                    txdetails.sign_and_broadcast()
                } else {
                    var dialog = app.messageDialog.createObject(app, {
                        text: qsTr('CPFP fee bump transaction created.') + '\n\n' + qsTr('You still need to sign and broadcast this transaction.')
                    })
                    dialog.open()
                }
            }
            onClosed: destroy()
        }
    }

    Component {
        id: rbfCancelDialog
        RbfCancelDialog {
            id: dialog
            txcanceller: TxCanceller {
                id: txcanceller
                wallet: Daemon.currentWallet
                txid: dialog.txid
            }

            onTxaccepted: {
                root.rawtx = txcanceller.getNewTx()
                if (txdetails.wallet.canSignWithoutCosigner) {
                    txdetails.sign_and_broadcast()
                } else {
                    var dialog = app.messageDialog.createObject(app, {
                        text: qsTr('Cancel transaction created.') + '\n\n' + qsTr('You still need to sign and broadcast this transaction.')
                    })
                    dialog.open()
                }
            }
            onClosed: destroy()
        }
    }

}
