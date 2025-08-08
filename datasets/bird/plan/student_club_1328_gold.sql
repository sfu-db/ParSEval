{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 2,
      "name": "$2",
      "type": "VARCHAR"
    }
  ],
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "EQUALS",
        "operator": "=",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 12,
            "name": "$12",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "Laugh Out Loud",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
          }
        ]
      },
      "variableset": "[]",
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 9,
                "name": "$9",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 11,
                "name": "$11",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 0,
                    "name": "$0",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 10,
                    "name": "$10",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "member",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "attendance",
                  "id": "1",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "event",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}