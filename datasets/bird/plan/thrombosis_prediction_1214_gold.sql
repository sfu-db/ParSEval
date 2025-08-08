{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "INTEGER"
    },
    {
      "kind": "CASE",
      "operator": "CASE",
      "type": "CHAR",
      "operands": [
        {
          "kind": "LESS_THAN",
          "operator": "<",
          "type": "BOOLEAN",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 12,
              "name": "$12",
              "type": "INTEGER"
            },
            {
              "kind": "LITERAL",
              "value": 300,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        },
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "CHAR",
          "operands": [
            {
              "kind": "LITERAL",
              "value": "normal",
              "type": "CHAR",
              "nullable": false,
              "precision": 6
            }
          ]
        },
        {
          "kind": "LITERAL",
          "value": "abNormal",
          "type": "CHAR",
          "nullable": false,
          "precision": 8
        }
      ]
    }
  ],
  "id": "4",
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
            "index": 2,
            "name": "$2",
            "type": "DATE"
          },
          {
            "kind": "CAST",
            "operator": "CAST",
            "type": "DATE",
            "operands": [
              {
                "kind": "LITERAL",
                "value": "1982-04-01",
                "type": "CHAR",
                "nullable": false,
                "precision": 10
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
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
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 7,
                "name": "$7",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Patient",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "Laboratory",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}